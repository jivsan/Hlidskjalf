"""PrometheusSource (datasources/prometheus.py) — the phase 2 metrics source.

Runs dev/mock_prometheus.py as a real uvicorn subprocess (same style as the mock
PVE in conftest.py), so the httpx client, the query_range wire format and the
row-shaping all get exercised offline.

Covers: row-shape conformance with the MetricsSource protocol (identical keys to
RRDSource), timeframe -> step mapping, alignment of several series into one row
per step, None-fill for missing series, graceful degradation when Prometheus is
down, config/startup errors, and that the default rrd path is untouched.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from hlidskjalf.config import Settings, get_settings
from hlidskjalf.datasources.prometheus import (
    TIMEFRAMES,
    PrometheusSource,
    _merge,
    _parse_value,
)
from hlidskjalf.datasources.rrd import NODE_FIELDS, VM_FIELDS, RRDSource
from hlidskjalf.pve import PveClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "dev"

VM_KEYS = {"t", *VM_FIELDS}
NODE_KEYS = {"t", *NODE_FIELDS}


def _free_port() -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_prom_url() -> str:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "mock_prometheus:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=DEV_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"mock_prometheus exited early: {proc.stderr.read().decode(errors='replace')}"
            )
        try:
            if httpx.get(f"{url}/-/healthy", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            proc.terminate()
            raise RuntimeError("mock_prometheus did not become ready within 20s")
        time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def _settings(url: str, **kw) -> Settings:
    return Settings(
        metrics_source="prometheus", prometheus_url=url, pve_node="hella", **kw
    )


@pytest.fixture()
async def source(mock_prom_url):
    src = PrometheusSource(_settings(mock_prom_url))
    yield src
    await src.aclose()


# --- config / startup --------------------------------------------------------


def test_requires_prometheus_url():
    with pytest.raises(RuntimeError, match="HLIDSKJALF_PROMETHEUS_URL"):
        PrometheusSource(Settings(metrics_source="prometheus", prometheus_url=""))


def test_default_source_is_rrd_and_untouched(client):
    """A deployment that sets none of the new vars behaves exactly as before."""
    assert Settings().metrics_source == "rrd"
    assert Settings().prometheus_url == ""
    assert isinstance(client.app.state.metrics, RRDSource)


def test_rrd_metrics_endpoint_still_works(auth_client):
    r = auth_client.get("/api/vms/105/metrics?timeframe=hour")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows and set(rows[0]) == VM_KEYS


# --- timeframe -> step -------------------------------------------------------


def test_timeframe_step_mapping():
    assert PrometheusSource.window_step("hour") == (4200, 60)
    assert PrometheusSource.window_step("day") == (126000, 1800)
    assert PrometheusSource.window_step("week") == (504000, 7200)
    # month/year keep the rrd window but use a finer step than rrddata's RRA
    assert PrometheusSource.window_step("month") == (70 * 43200, 3600)
    assert PrometheusSource.window_step("year") == (70 * 604800, 86400)
    # unknown timeframe falls back to hour rather than exploding
    assert PrometheusSource.window_step("decade") == TIMEFRAMES["hour"]


async def test_step_drives_row_spacing(source):
    for timeframe in ("hour", "day", "week"):
        rows = await source.get_vm_series(105, "qemu", timeframe, "AVERAGE")
        _, step = PrometheusSource.window_step(timeframe)
        assert len(rows) > 2
        deltas = {rows[i + 1]["t"] - rows[i]["t"] for i in range(len(rows) - 1)}
        assert deltas == {step}


# --- query construction ------------------------------------------------------


def test_vm_queries_shape(source):
    q = source._vm_queries(105, "qemu", 60, "AVERAGE")
    assert set(q) == set(VM_FIELDS)
    assert q["cpu"] == 'avg_over_time(pve_cpu_usage_ratio{id="qemu/105"}[60s])'
    # counters become bytes/sec, the unit rrddata reports
    assert q["netin"] == 'rate(pve_network_receive_bytes{id="qemu/105"}[300s])'
    assert source._vm_queries(130, "lxc", 60, "AVERAGE")["mem"].endswith('{id="lxc/130"}[60s])')


def test_cf_max_uses_max_over_time(source):
    q = source._vm_queries(105, "qemu", 3600, "MAX")
    assert q["cpu"].startswith("max_over_time(")
    assert q["netin"] == 'max_over_time(rate(pve_network_receive_bytes{id="qemu/105"}[3600s])[3600s:3600s])'


def test_node_queries_omit_unexported_fields(source):
    q = source._node_queries(60, "AVERAGE")
    assert 'id="node/hella"' in q["cpu"]
    # prometheus-pve-exporter has no node iowait/loadavg/netin/netout
    assert not {"iowait", "loadavg", "netin", "netout"} & set(q)


def test_node_query_overrides(mock_prom_url):
    src = PrometheusSource(
        _settings(
            mock_prom_url,
            prometheus_node_queries={
                "loadavg": 'node_load1{instance="$node:9100"}',
                "iowait": 'rate(node_cpu_seconds_total{mode="iowait"}[$steps])',
                "bogus": "nope",  # unknown field: ignored, not injected
            },
        )
    )
    q = src._node_queries(300, "AVERAGE")
    assert q["loadavg"] == 'node_load1{instance="hella:9100"}'
    assert q["iowait"] == 'rate(node_cpu_seconds_total{mode="iowait"}[300s])'
    assert "bogus" not in q


async def test_node_override_series_lands_in_rows(mock_prom_url):
    src = PrometheusSource(
        _settings(
            mock_prom_url,
            prometheus_node_queries={"loadavg": 'node_load1{id="node/$node"}'},
        )
    )
    try:
        rows = await src.get_node_series("hour", "AVERAGE")
        assert rows
        assert any(r["loadavg"] is not None for r in rows)
        assert all(isinstance(r["loadavg"], float) or r["loadavg"] is None for r in rows)
    finally:
        await src.aclose()


# --- row shape / alignment ---------------------------------------------------


async def test_vm_rows_match_protocol_shape(source):
    rows = await source.get_vm_series(105, "qemu", "hour", "AVERAGE")
    assert rows
    for row in rows:
        assert set(row) == VM_KEYS
        assert isinstance(row["t"], int)
        for f in VM_FIELDS:
            assert row[f] is None or isinstance(row[f], float)
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)
    # values actually made it through (not an all-None degradation)
    assert all(r["cpu"] is not None and r["netin"] is not None for r in rows)


async def test_vm_rows_have_identical_keys_to_rrd(source, mock_pve_server):
    """Drop-in guarantee: same keys as the rrd source the frontend was built on."""
    # A private PveClient (against dev/mock_pve.py) — app.state.pve belongs to the
    # TestClient's portal loop and must not be driven from this one (see conftest).
    pve = PveClient(get_settings())
    try:
        rrd = RRDSource(pve)
        assert set((await source.get_vm_series(105, "qemu", "hour", "AVERAGE"))[0]) == set(
            (await rrd.get_vm_series(105, "qemu", "hour", "AVERAGE"))[0]
        )
        assert set((await source.get_node_series("hour", "AVERAGE"))[0]) == set(
            (await rrd.get_node_series("hour", "AVERAGE"))[0]
        )
    finally:
        await pve.aclose()


async def test_missing_series_becomes_none(source):
    """The mock exporter has no disk usage for containers -> None, other fields intact."""
    rows = await source.get_vm_series(130, "lxc", "hour", "AVERAGE")
    assert rows
    assert all(r["disk"] is None for r in rows)
    assert all(r["maxdisk"] is not None for r in rows)


async def test_unknown_guest_yields_empty_series(source):
    assert await source.get_vm_series(4242, "qemu", "hour", "AVERAGE") == []


async def test_node_rows_match_protocol_shape(source):
    rows = await source.get_node_series("day", "AVERAGE")
    assert rows
    for row in rows:
        assert set(row) == NODE_KEYS
        for f in NODE_FIELDS:
            assert row[f] is None or isinstance(row[f], float)
    # exported by pve-exporter
    assert all(r["cpu"] is not None and r["memtotal"] is not None for r in rows)
    # not exported, and no override configured
    assert all(r["iowait"] is None and r["loadavg"] is None for r in rows)


def test_merge_aligns_partial_series():
    merged = _merge(
        {
            "cpu": {100: 0.5, 160: 0.6, 220: 0.7},
            "mem": {160: 2048.0},            # only the middle step
            "netin": {100: 10.0, 220: 30.0},  # gap in the middle
        },
        VM_FIELDS,
    )
    assert [r["t"] for r in merged] == [100, 160, 220]
    assert [r["cpu"] for r in merged] == [0.5, 0.6, 0.7]
    assert [r["mem"] for r in merged] == [None, 2048.0, None]
    assert [r["netin"] for r in merged] == [10.0, None, 30.0]
    # fields with no series at all are present and None (never dropped)
    assert all(set(r) == VM_KEYS for r in merged)
    assert all(r["maxdisk"] is None for r in merged)


def test_merge_empty_is_empty():
    assert _merge({}, VM_FIELDS) == []


def test_parse_value_rejects_nan_and_inf():
    # NaN/Inf are not valid JSON — they must never reach the frontend
    assert _parse_value("1.5") == 1.5
    assert _parse_value("NaN") is None
    assert _parse_value("+Inf") is None
    assert _parse_value(None) is None
    assert _parse_value("banana") is None


# --- graceful degradation ----------------------------------------------------


async def test_prometheus_down_degrades_to_empty():
    """Prometheus unreachable: warn + empty rows, never an exception (no 500)."""
    src = PrometheusSource(_settings(f"http://127.0.0.1:{_free_port()}"))
    try:
        assert await src.get_vm_series(105, "qemu", "hour", "AVERAGE") == []
        assert await src.get_node_series("hour", "AVERAGE") == []
    finally:
        await src.aclose()


async def test_prometheus_error_response_degrades(mock_prom_url, monkeypatch):
    src = PrometheusSource(_settings(mock_prom_url))

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "error", "errorType": "bad_data", "error": "parse error"}

    async def _get(*a, **kw):
        return _Resp()

    monkeypatch.setattr(src._client, "get", _get)
    try:
        assert await src.get_vm_series(105, "qemu", "hour", "AVERAGE") == []
    finally:
        await src.aclose()


async def test_partial_failure_keeps_other_fields(source, monkeypatch):
    """One failing query must not sink the whole series."""
    real = source._query_range

    async def flaky(query: str, start: int, end: int, step: int):
        if "pve_memory_usage_bytes" in query:
            raise httpx.ConnectError("boom")
        return await real(query, start, end, step)

    monkeypatch.setattr(source, "_query_range", flaky)
    rows = await source.get_vm_series(105, "qemu", "hour", "AVERAGE")
    assert rows
    assert all(r["mem"] is None for r in rows)
    assert all(r["cpu"] is not None for r in rows)


def test_routes_serve_prometheus_rows(mock_prom_url, client, auth_client, monkeypatch):
    """End to end through /api/vms/{id}/metrics with the prometheus source wired.

    The source is built here (sync) and used only from inside the TestClient's
    portal loop, which is the loop its httpx client then binds to.
    """
    monkeypatch.setattr(client.app.state, "metrics", PrometheusSource(_settings(mock_prom_url)))
    r = auth_client.get("/api/vms/105/metrics?timeframe=hour&cf=AVERAGE")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows and set(rows[0]) == VM_KEYS

    r = auth_client.get("/api/node/metrics?timeframe=hour")
    assert r.status_code == 200, r.text
    assert set(r.json()[0]) == NODE_KEYS
