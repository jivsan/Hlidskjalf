"""PrometheusSource — long-range metrics from heimdall's Prometheus (plan.md §8).

Drop-in alternative to ``RRDSource``: same ``MetricsSource`` protocol, same row
shapes (``datasources/rrd.py`` owns ``VM_FIELDS`` / ``NODE_FIELDS`` — imported
here so the two can never drift), so the frontend charts need zero changes.
Selected with ``HLIDSKJALF_METRICS_SOURCE=prometheus``; ``rrd`` stays the default.

Data comes from ``prometheus-pve-exporter`` (nixpkgs
``services.prometheus.exporters.pve``) scraping hella with the same
``hlidskjalf@pve!panel`` token, queried through the Prometheus HTTP API
(``/api/v1/query_range``). The exporter derives its series from PVE's
``/cluster/resources``, so every metric is labelled ``id="qemu/105"`` /
``id="lxc/130"`` / ``id="node/hella"``.

Semantics matched to rrddata (see the frontend's ``VmMetricPoint``):
- ``cpu`` is a 0..1 ratio, ``mem``/``disk`` are bytes — gauges, consolidated
  over the step with ``avg_over_time`` (cf=AVERAGE) or ``max_over_time`` (MAX).
- ``netin``/``netout``/``diskread``/``diskwrite`` are **bytes/sec** in rrddata,
  while the exporter publishes cumulative counters — so they are wrapped in
  ``rate()`` (which also absorbs the counter reset when a guest reboots).

Windows are identical to PVE's rrd timeframes; only the month/year *steps* are
finer (rrddata's month RRA is 12h-coarse — the whole point of phase 2).

Robustness: a Prometheus that is down, slow or angry degrades gracefully — every
query is awaited independently, failures are logged as warnings and their field
becomes ``None``; if everything fails the series is simply empty. The metrics
endpoint never 500s because of Prometheus (same posture as ``switch.py``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import ssl
import time
from typing import Union

import httpx

from ..config import Settings
from ..pve import make_pinned_ssl_context
from .rrd import NODE_FIELDS, VM_FIELDS

log = logging.getLogger(__name__)

# timeframe -> (window seconds, step seconds).
#
# The windows are exactly PVE's rrd timeframes (70 points x the rrd step), so a
# chart covers the same span whichever source is configured. hour/day/week keep
# the rrd step too; month and year use a finer step because Prometheus keeps the
# raw samples and coarse rrd consolidation is what phase 2 exists to fix
# (month: 12h -> 1h, 840 points; year: 1w -> 1d, ~490 points).
TIMEFRAMES: dict[str, tuple[int, int]] = {
    "hour": (70 * 60, 60),            # ~70 min, 1 min steps
    "day": (70 * 1800, 1800),         # ~35 h, 30 min steps
    "week": (70 * 7200, 7200),        # ~5.8 d, 2 h steps
    "month": (70 * 43200, 3600),      # ~35 d, 1 h steps   (rrd: 12 h)
    "year": (70 * 604800, 86400),     # ~1.3 y, 1 d steps  (rrd: 1 w)
}
DEFAULT_TIMEFRAME = "hour"

# Lower bound for a rate() window: it must span several scrape intervals (the
# exporter is typically scraped every 15-60s) or rate() yields nothing.
MIN_RATE_WINDOW = 300

_warned_unverified = False


def _warn_unverified_once() -> None:
    global _warned_unverified
    if not _warned_unverified:
        log.warning(
            "Prometheus TLS verification is DISABLED "
            "(HLIDSKJALF_PROMETHEUS_VERIFY=false): queries and any bearer token "
            "are sent over an UNVERIFIED connection. Set "
            "HLIDSKJALF_PROMETHEUS_FINGERPRINT to pin the cert instead."
        )
        _warned_unverified = True


def select_prometheus_verify(settings: Settings) -> Union[ssl.SSLContext, bool]:
    """httpx ``verify`` argument for the Prometheus connection.

    Mirrors ``switch.select_switch_verify``: a pinned SHA-256 fingerprint wins
    (same mechanism as PVE), else system CAs, else — explicitly — no verification
    with a one-time warning. Irrelevant for a plain-http ``prometheus_url``.
    """
    if settings.prometheus_fingerprint:
        return make_pinned_ssl_context(settings.prometheus_fingerprint)
    if not settings.prometheus_verify:
        _warn_unverified_once()
        return False
    return True


def _parse_value(raw) -> float | None:
    """Prometheus sample value (a string) -> float, or None.

    NaN/Inf become None on purpose: they are not representable in JSON and would
    otherwise be emitted as bare `NaN` tokens that the frontend's JSON.parse rejects.
    """
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _merge(series: dict[str, dict[int, float | None]], fields: tuple[str, ...]) -> list[dict]:
    """Align per-field {timestamp: value} maps into one row per step.

    Union of all timestamps, ascending; a field with no sample at a timestamp
    (missing series, guest stopped, exporter restarted) is None — never dropped,
    never invented.
    """
    timestamps = sorted({t for values in series.values() for t in values})
    rows: list[dict] = []
    for t in timestamps:
        row: dict = {"t": t}
        for f in fields:
            v = series.get(f, {}).get(t)
            row[f] = float(v) if v is not None else None
        rows.append(row)
    return rows


class PrometheusSource:
    def __init__(self, settings: Settings):
        if not settings.prometheus_url:
            raise RuntimeError(
                "HLIDSKJALF_PROMETHEUS_URL is required with "
                "HLIDSKJALF_METRICS_SOURCE=prometheus (e.g. http://10.0.20.17:9090)"
            )
        self.settings = settings
        self.node = settings.pve_node
        self.base_url = settings.prometheus_url.rstrip("/")

        headers = {}
        if settings.prometheus_token:
            headers["Authorization"] = f"Bearer {settings.prometheus_token}"
        auth = (
            (settings.prometheus_username, settings.prometheus_password)
            if settings.prometheus_username
            else None
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            verify=select_prometheus_verify(settings),
            timeout=httpx.Timeout(settings.prometheus_timeout, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- MetricsSource protocol ---------------------------------------------

    async def get_vm_series(self, vmid: int, kind: str, timeframe: str, cf: str) -> list[dict]:
        window, step = self.window_step(timeframe)
        return await self._series(self._vm_queries(vmid, kind, step, cf), VM_FIELDS, window, step)

    async def get_node_series(self, timeframe: str, cf: str) -> list[dict]:
        window, step = self.window_step(timeframe)
        return await self._series(self._node_queries(step, cf), NODE_FIELDS, window, step)

    # --- queries -------------------------------------------------------------

    @staticmethod
    def window_step(timeframe: str) -> tuple[int, int]:
        return TIMEFRAMES.get(timeframe, TIMEFRAMES[DEFAULT_TIMEFRAME])

    @staticmethod
    def _gauge(metric: str, selector: str, step: int, cf: str) -> str:
        """Gauge consolidated over the step — rrd's AVERAGE / MAX equivalent."""
        fn = "max_over_time" if cf.upper() == "MAX" else "avg_over_time"
        return f"{fn}({metric}{selector}[{step}s])"

    @staticmethod
    def _counter(metric: str, selector: str, step: int, cf: str) -> str:
        """Cumulative counter -> bytes/sec, the unit rrddata reports."""
        rate_window = max(step, MIN_RATE_WINDOW)
        expr = f"rate({metric}{selector}[{rate_window}s])"
        if cf.upper() == "MAX":
            # peak rate inside the step, not the rate at the step boundary
            return f"max_over_time({expr}[{step}s:{rate_window}s])"
        return expr

    def _vm_queries(self, vmid: int, kind: str, step: int, cf: str) -> dict[str, str]:
        kind = "lxc" if kind == "lxc" else "qemu"  # never interpolate anything else
        sel = f'{{id="{kind}/{int(vmid)}"}}'
        g, c = self._gauge, self._counter
        return {
            "cpu": g("pve_cpu_usage_ratio", sel, step, cf),
            "maxcpu": g("pve_cpu_usage_limit", sel, step, cf),
            "mem": g("pve_memory_usage_bytes", sel, step, cf),
            "maxmem": g("pve_memory_size_bytes", sel, step, cf),
            "disk": g("pve_disk_usage_bytes", sel, step, cf),
            "maxdisk": g("pve_disk_size_bytes", sel, step, cf),
            "diskread": c("pve_disk_read_bytes", sel, step, cf),
            "diskwrite": c("pve_disk_write_bytes", sel, step, cf),
            "netin": c("pve_network_receive_bytes", sel, step, cf),
            "netout": c("pve_network_transmit_bytes", sel, step, cf),
        }

    def _node_queries(self, step: int, cf: str) -> dict[str, str]:
        sel = f'{{id="node/{self.node}"}}'
        g = self._gauge
        queries = {
            "cpu": g("pve_cpu_usage_ratio", sel, step, cf),
            "maxcpu": g("pve_cpu_usage_limit", sel, step, cf),
            "memused": g("pve_memory_usage_bytes", sel, step, cf),
            "memtotal": g("pve_memory_size_bytes", sel, step, cf),
            "rootused": g("pve_disk_usage_bytes", sel, step, cf),
            "roottotal": g("pve_disk_size_bytes", sel, step, cf),
            # iowait / loadavg / netin / netout have no counterpart in
            # prometheus-pve-exporter (PVE's /cluster/resources exposes none of
            # them for a node), so they stay None unless the operator supplies
            # PromQL via HLIDSKJALF_PROMETHEUS_NODE_QUERIES — typically pointing
            # at a node_exporter running on hella.
        }
        for field, expr in (self.settings.prometheus_node_queries or {}).items():
            if field not in NODE_FIELDS:
                log.warning(
                    "HLIDSKJALF_PROMETHEUS_NODE_QUERIES: ignoring unknown node field %r "
                    "(known: %s)", field, ", ".join(NODE_FIELDS),
                )
                continue
            queries[field] = expr.replace("$step", str(step)).replace("$node", self.node)
        return queries

    # --- HTTP ----------------------------------------------------------------

    async def _series(
        self, queries: dict[str, str], fields: tuple[str, ...], window: int, step: int
    ) -> list[dict]:
        end = int(time.time()) // step * step  # align to the step grid
        start = end - window

        results = await asyncio.gather(
            *(self._query_range(q, start, end, step) for q in queries.values()),
            return_exceptions=True,
        )
        series: dict[str, dict[int, float | None]] = {}
        for field, result in zip(queries, results):
            if isinstance(result, BaseException):
                # Prometheus down / unreachable / query rejected: that field
                # degrades to None instead of taking the endpoint down with it.
                log.warning("prometheus query for %s failed: %s", field, result)
                continue
            series[field] = result
        return _merge(series, fields)

    async def _query_range(
        self, query: str, start: int, end: int, step: int
    ) -> dict[int, float | None]:
        resp = await self._client.get(
            "/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"prometheus HTTP {resp.status_code} for {query!r}")
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus error for {query!r}: {body.get('error')}")

        result = (body.get("data") or {}).get("result") or []
        if not result:
            return {}  # series absent (metric not scraped, guest never seen) -> None fields
        if len(result) > 1:
            # An `id` selects exactly one series; more means duplicate scrape targets.
            log.warning(
                "prometheus returned %d series for %r — using the first", len(result), query
            )
        out: dict[int, float | None] = {}
        for sample in result[0].get("values") or []:
            if not isinstance(sample, (list, tuple)) or len(sample) != 2:
                continue
            ts = _parse_value(sample[0])
            if ts is None:
                continue
            out[int(round(ts))] = _parse_value(sample[1])
        return out
