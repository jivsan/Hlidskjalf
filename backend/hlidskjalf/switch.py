"""Arista EOS switch client for port visualization.

Prefers eAPI (recommended - enable on switch with 'management api http-commands').
Falls back to SSH + text parsing using paramiko if eAPI is disabled.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import paramiko

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


class AristaClient:
    def __init__(self):
        s = get_settings()
        self.host = s.switch_host
        self.port = s.switch_port
        self.username = s.switch_username
        self.password = s.switch_password
        self.use_eapi = s.switch_use_eapi
        self.ssh_port = s.switch_ssh_port

    async def get_ports(self) -> list[PortInfo]:
        """Return list of ports with status, rates, descriptions."""
        if self.use_eapi and self.username and self.password:
            try:
                return await self._get_ports_eapi()
            except Exception:
                # fall through to SSH
                pass

        return await self._get_ports_ssh()

    async def _get_ports_eapi(self) -> list[PortInfo]:
        """Use Arista eAPI (JSON-RPC over HTTPS)."""
        base = f"https://{self.host}:{self.port}"
        auth = (self.username, self.password)

        cmds = [
            "show interfaces status",
            "show interfaces description",
            "show interfaces counters rates",
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
        if len(results) < 3:
            return []

        status_data = results[0]
        desc_data = results[1]
        counters_data = results[2]

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
                )
            )

        # Sort nicely (Ethernet1, Ethernet2, ...)
        ports.sort(key=lambda p: self._port_sort_key(p.name))
        return ports

    async def _get_ports_ssh(self) -> list[PortInfo]:
        """SSH fallback using paramiko + simple parsing."""
        if not (self.username and self.password):
            return []

        def _ssh_work() -> list[PortInfo]:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                self.host,
                port=self.ssh_port,
                username=self.username,
                password=self.password,
                timeout=8,
                look_for_keys=False,
            )

            ports: list[PortInfo] = []

            # 1. interfaces status
            stdin, stdout, _ = client.exec_command("show interfaces status | json")
            status_raw = stdout.read().decode()
            try:
                status_data = json.loads(status_raw)
            except Exception:
                status_data = {}

            # 2. descriptions
            stdin, stdout, _ = client.exec_command("show interfaces description | json")
            desc_raw = stdout.read().decode()
            try:
                desc_data = json.loads(desc_raw)
            except Exception:
                desc_data = {}

            desc_map = {
                i.get("interface", ""): i.get("description", "")
                for i in desc_data.get("interfaceDescriptions", [])
            }

            # 3. rates (if available)
            try:
                stdin, stdout, _ = client.exec_command("show interfaces counters rates | json")
                rate_raw = stdout.read().decode()
                rate_data = json.loads(rate_raw)
            except Exception:
                rate_data = {}

            rate_map = {}
            for iface, stats in rate_data.get("interfaces", {}).items():
                rate_map[iface] = (
                    int(stats.get("inBitsRate", 0) or 0),
                    int(stats.get("outBitsRate", 0) or 0),
                )

            for name, info in status_data.get("interfaceStatuses", {}).items():
                if not name.lower().startswith(("ethernet", "et")):
                    continue

                status = info.get("linkStatus", "notconnect")
                speed = str(info.get("bandwidth", ""))
                duplex = info.get("duplex", "")
                vlan = str(info.get("vlanInformation", {}).get("vlanId", "")) or None

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
                    )
                )

            client.close()
            ports.sort(key=lambda p: self._port_sort_key(p.name))
            return ports

        return await asyncio.to_thread(_ssh_work)

    def _port_sort_key(self, name: str) -> tuple:
        # Sort Ethernet1, Ethernet1/1, etc.
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
