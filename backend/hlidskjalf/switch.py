"""Arista EOS switch client for port visualization (eAPI only).

Enable eAPI on switch:
  management api http-commands
    protocol https
    no shutdown

Fetches ports, LLDP neighbors, descriptions, counters for visualization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .config import get_settings


@dataclass
class PortInfo:
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

    async def get_ports(self) -> list[PortInfo]:
        """Return list of ports with status, rates, descriptions, LLDP (pure eAPI, no SSH fallback)."""
        if not (self.username and self.password):
            return []
        return await self._get_ports_eapi()

    async def _get_ports_eapi(self) -> list[PortInfo]:
        """Use Arista eAPI (JSON-RPC over HTTPS)."""
        base = f"https://{self.host}:{self.port}"
        auth = (self.username, self.password)

        cmds = [
            "show interfaces status",
            "show interfaces description",
            "show interfaces counters rates",
            "show lldp neighbors",
        ]

        payload = {
            "jsonrpc": "2.0",
            "method": "runCmds",
            "params": {
                "version": 1,
                "cmds": cmds,
                "format": "json",
            },
            "id": "hlidskjalf-switch",
        }

        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            resp = await client.post(f"{base}/command", json=payload, auth=auth)
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            raise RuntimeError(data["error"])

        results = data.get("result", [])
        if len(results) < 4:
            return []

        status_data = results[0]
        desc_data = results[1]
        counters_data = results[2]
        lldp_data = results[3]

        # Build description map
        desc_map: dict[str, str] = {}
        for iface in desc_data.get("interfaceDescriptions", []):
            name = iface.get("interface", "")
            desc = iface.get("description", "") or ""
            desc_map[name] = desc

        # Build counters
        rate_map: dict[str, tuple[int, int]] = {}
        for iface, stats in counters_data.get("interfaces", {}).items():
            in_rate = int(stats.get("inBitsRate", 0) or 0)
            out_rate = int(stats.get("outBitsRate", 0) or 0)
            rate_map[iface] = (in_rate, out_rate)

        # Build LLDP map (simple: last neighbor per local port)
        lldp_map: dict[str, dict] = {}
        for neigh in lldp_data.get("lldpNeighbors", []):
            local_if = neigh.get("port", "")
            system = neigh.get("neighborDevice", "") or neigh.get("systemName", "")
            neigh_port = neigh.get("neighborPort", "") or ""
            if local_if and system:
                lldp_map[local_if] = {"system_name": system, "port": neigh_port}

        ports: list[PortInfo] = []
        for name, info in status_data.get("interfaceStatuses", {}).items():
            if not name.lower().startswith(("ethernet", "et")):
                continue  # only physical ports for now

            status = info.get("linkStatus", "notconnect")
            speed = info.get("bandwidth", "auto") or ""
            duplex = info.get("duplex", "")
            vlan = str(info.get("vlanInformation", {}).get("vlanId", "")) or None

            in_r, out_r = rate_map.get(name, (0, 0))
            active = (in_r > 1000) or (out_r > 1000)  # >1kbps = active

            ports.append(
                PortInfo(
                    name=name,
                    status=status,
                    speed=str(speed),
                    duplex=duplex,
                    vlan=vlan,
                    description=desc_map.get(name, ""),
                    input_rate=in_r,
                    output_rate=out_r,
                    active=active,
                    lldp_neighbor=lldp_map.get(name),
                )
            )

        # Sort nicely (Ethernet1, Ethernet2, ...)
        ports.sort(key=lambda p: self._port_sort_key(p.name))
        return ports

    def _port_sort_key(self, name: str) -> tuple:
        # Sort Ethernet1, Ethernet2, etc.
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