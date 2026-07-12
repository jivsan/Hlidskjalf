"""Arista EOS switch client for port visualization (eAPI only).

Enable eAPI on switch:
  management api http-commands
    protocol https
    no shutdown

Fetches ports, LLDP neighbors, descriptions, counters for visualization.

Robustness:
- 2.5s TTL cache (rate limit against switch eAPI)
- retry with exponential backoff on transient errors
- handles timeouts, auth failures (401/ eapi err), json/malformed, partial data
- normalizes port names across Arista output variations (EthernetN, EtN, etN, ethN)
- validation + safe defaults for every field
- on any failure: fallback to [] (or stale cache if available) for graceful UI

Edge cases handled (see comments):
- no switch configured (no user/pass/host) -> []
- auth fail / bad creds -> []
- partial LLDP (only some ports have neighbors)
- high port counts (52+), non-physical interfaces filtered
- missing keys / null rates / non-numeric -> safe 0 / ""
- empty results list from eAPI
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Union

import httpx

from .config import Settings, get_settings
from .pve import make_pinned_ssl_context

log = logging.getLogger(__name__)

_warned_switch_unverified = False


def _warn_switch_unverified_once() -> None:
    global _warned_switch_unverified
    if not _warned_switch_unverified:
        log.warning(
            "switch eAPI TLS verification is DISABLED (HLIDSKJALF_SWITCH_VERIFY=false): "
            "the switch admin credentials are sent over an UNVERIFIED connection and can "
            "be captured by a MITM. Set HLIDSKJALF_SWITCH_FINGERPRINT to pin the cert."
        )
        _warned_switch_unverified = True


def select_switch_verify(settings: Settings) -> Union[ssl.SSLContext, bool]:
    """Choose the httpx ``verify`` argument for the switch eAPI connection.

    - ``switch_fingerprint`` set   -> a pinned SSL context (same mechanism as PVE);
      the eAPI cert is accepted only if its SHA-256 digest matches.
    - else ``switch_verify`` False -> ``False`` (no verification) + one-time WARNING.
    - else                          -> ``True`` (verify against system CAs).
    """
    if settings.switch_fingerprint:
        return make_pinned_ssl_context(settings.switch_fingerprint)
    if not settings.switch_verify:
        _warn_switch_unverified_once()
        return False
    return True


@dataclass
class PortInfo:
    """Typed port info returned to routes + frontend.
    All fields have safe defaults; never None where UI expects string/number.
    """
    name: str
    status: str          # "connected", "notconnect", "disabled", etc.
    speed: str
    duplex: str
    vlan: str | None
    description: str     # from switch 'show interfaces description'
    note: str = ""       # user note from panel DB
    input_rate: int = 0  # bps
    output_rate: int = 0 # bps
    active: bool = False # for UI blinking
    lldp_neighbor: Optional[dict] = field(default=None)  # e.g. {"system_name": "...", "port": "..."} from LLDP


class AristaClient:
    def __init__(self):
        s = get_settings()
        self.host = s.switch_host
        self.port = s.switch_port
        self.username = s.switch_username
        self.password = s.switch_password
        # httpx `verify` arg for eAPI TLS: pinned SSL context, True, or False.
        self._verify: Union[ssl.SSLContext, bool] = select_switch_verify(s)

        # Simple in-memory TTL cache + rate limiting (2-3s as specified)
        self._cache: list[PortInfo] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 2.5
        self._lock = asyncio.Lock()

    async def get_ports(self) -> list[PortInfo]:
        """Return list of ports with status, rates, descriptions, LLDP (pure eAPI, no SSH fallback).

        Caching: serves from cache if <2.5s old. On eAPI failure, serves stale cache
        if present (last-known for UI) else empty list (graceful degradation).
        Never crashes caller.
        """
        if not (self.host and self.username and self.password):
            # no switch configured (common dev/prod case)
            return []

        now = time.monotonic()
        if self._cache and (now - self._cache_ts) < self._cache_ttl:
            return list(self._cache)

        async with self._lock:
            now = time.monotonic()  # recheck under lock
            if self._cache and (now - self._cache_ts) < self._cache_ttl:
                return list(self._cache)

            try:
                ports = await self._get_ports_eapi()
                self._cache = ports
                self._cache_ts = time.monotonic()
                return list(ports)
            except Exception as exc:
                log.warning("switch eAPI failure (serving fallback): %s", exc)
                # fallback: stale last-known if we have it, else empty
                # frontend will show "offline" banner but can keep rendering last data
                return list(self._cache) if self._cache else []

    async def _get_ports_eapi(self) -> list[PortInfo]:
        """Use Arista eAPI (JSON-RPC over HTTPS). With retry + validation."""
        if not self.host:
            return []

        base = f"https://{self.host}:{self.port}"
        auth = (self.username, self.password)

        cmds = [
            "show interfaces status",
            "show interfaces description",
            "show interfaces counters rates",
            "show lldp neighbors",
        ]

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "runCmds",
            "params": {
                "version": 1,
                "cmds": cmds,
                "format": "json",
            },
            "id": "hlidskjalf-switch",
        }

        data = await self._eapi_request_with_retry(base, auth, payload)

        if not isinstance(data, dict):
            raise RuntimeError("malformed eAPI response (not object)")

        if "error" in data:
            err = data["error"]
            msg = str(err) if not isinstance(err, dict) else err.get("message", str(err))
            # auth failures surface here or as 401 from httpx
            raise RuntimeError(f"eAPI error: {msg}")

        results = data.get("result")
        if not isinstance(results, list) or len(results) < 4:
            # partial or bad batch response
            return []

        status_data = results[0] or {}
        desc_data = results[1] or {}
        counters_data = results[2] or {}
        lldp_data = results[3] or {}

        # Build maps with normalized port names (handles Et1 / Ethernet1 / et1 variations)
        desc_map: dict[str, str] = {}
        for iface in desc_data.get("interfaceDescriptions", []) or []:
            if not isinstance(iface, dict):
                continue
            name = self._normalize_port_name(iface.get("interface", ""))
            desc = str(iface.get("description", "") or "")
            if name:
                desc_map[name] = desc

        rate_map: dict[str, tuple[int, int]] = {}
        for iface, stats in (counters_data.get("interfaces", {}) or {}).items():
            if not isinstance(stats, dict):
                continue
            iname = self._normalize_port_name(iface)
            try:
                in_rate = int(float(stats.get("inBitsRate", 0) or 0))
                out_rate = int(float(stats.get("outBitsRate", 0) or 0))
            except (ValueError, TypeError):
                in_rate = out_rate = 0
            if iname:
                rate_map[iname] = (in_rate, out_rate)

        lldp_map: dict[str, dict] = {}
        for neigh in lldp_data.get("lldpNeighbors", []) or []:
            if not isinstance(neigh, dict):
                continue
            local_if = self._normalize_port_name(neigh.get("port", ""))
            # Arista variations: neighborDevice or systemName
            system = str(neigh.get("neighborDevice", "") or neigh.get("systemName", "") or "")
            neigh_port = str(neigh.get("neighborPort", "") or "")
            if local_if and system:
                lldp_map[local_if] = {"system_name": system, "port": neigh_port}

        ports: list[PortInfo] = []
        for raw_name, info in (status_data.get("interfaceStatuses", {}) or {}).items():
            if not isinstance(info, dict):
                continue
            name = self._normalize_port_name(raw_name)
            if not name.lower().startswith("ethernet"):
                continue  # only physical Ethernet* (handles EtN, eth etc after norm)

            status = str(info.get("linkStatus", "notconnect") or "notconnect")
            speed = str(info.get("bandwidth", "auto") or "")
            duplex = str(info.get("duplex", "") or "")
            vlan_info = info.get("vlanInformation") or {}
            vlan = str(vlan_info.get("vlanId", "")) or None if vlan_info.get("vlanId") is not None else None

            in_r, out_r = rate_map.get(name, (0, 0))
            active = (in_r > 1000) or (out_r > 1000)

            ports.append(
                PortInfo(
                    name=name,
                    status=status,
                    speed=speed,
                    duplex=duplex,
                    vlan=vlan,
                    description=desc_map.get(name, ""),
                    input_rate=in_r,
                    output_rate=out_r,
                    active=active,
                    lldp_neighbor=lldp_map.get(name),
                )
            )

        ports.sort(key=lambda p: self._port_sort_key(p.name))
        return ports

    async def _eapi_request_with_retry(
        self, base: str, auth: tuple[str, str], payload: dict[str, Any], max_retries: int = 2
    ) -> dict:
        """POST to eAPI /command with timeout, auth, retry+backoff for transient issues."""
        delay = 0.12
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                # per-call client keeps things simple; short connect/read timeouts.
                # TLS verification per config (pinned fingerprint / system CAs / off).
                async with httpx.AsyncClient(verify=self._verify, timeout=httpx.Timeout(8.0, connect=4.0)) as client:
                    resp = await client.post(f"{base}/command", json=payload, auth=auth)
                    # explicit auth fail detection
                    if resp.status_code in (401, 403):
                        raise RuntimeError(f"auth failed (HTTP {resp.status_code})")
                    resp.raise_for_status()
                    return resp.json()
            except httpx.TimeoutException as e:
                last_exc = e
                log.info("eAPI timeout (attempt %d)", attempt + 1)
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code in (401, 403):
                    raise RuntimeError(f"auth failed (HTTP {e.response.status_code})") from e
            except (httpx.RequestError, ValueError, KeyError, TypeError) as e:
                # network, json decode, malformed
                last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 1.7  # backoff
        if last_exc:
            raise last_exc
        raise RuntimeError("eAPI request failed after retries")

    def _normalize_port_name(self, name: str) -> str:
        """Canonicalize Arista port names to 'EthernetN' (or 'EthernetN/M').

        Handles: "Ethernet1", "Et1", "et1", "Eth1", "ethernet1", "Et1/1", "Management1" (filtered later).
        """
        if not isinstance(name, str):
            return ""
        n = name.strip()
        if not n:
            return ""
        # extract leading letters + number suffix (support slashed subports)
        m = re.match(r"^([A-Za-z]+)(\d+(?:/\d+)*)$", n)
        if m:
            prefix = m.group(1).lower()
            num = m.group(2)
            if prefix.startswith(("eth", "et")):
                return f"Ethernet{num}"
        # fallback: try to find any number sequence
        m2 = re.search(r"(\d+(?:/\d+)*)", n)
        if m2 and re.search(r"et|ethernet", n, re.I):
            return f"Ethernet{m2.group(1)}"
        return n

    def _port_sort_key(self, name: str) -> tuple:
        """Sort Ethernet1, Ethernet2, Ethernet1/1 etc. numerically."""
        m = re.search(r"(\d+)(?:/(\d+))?", name)
        if m:
            return (int(m.group(1)), int(m.group(2) or 0))
        return (999, 0)


# Singleton helper
_client: AristaClient | None = None

def get_switch_client() -> AristaClient:
    global _client
    if _client is None:
        _client = AristaClient()
    return _client