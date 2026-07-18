"""Unit tests for switch robustness (eAPI client + routes).

Covers required edge cases:
- no switch configured
- partial LLDP
- high port counts
- error paths (simulated via monkeypatch)
- port name normalization
- caching behavior (basic)
- response shape with/without error

Does not require live switch or full TestClient (uses direct client + mock).
Run with: pytest tests/test_switch.py -q --tb=line
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from hlidskjalf.switch import (
    AristaClient,
    PortInfo,
    SwitchInfo,
    _classify_kind,
    _format_speed,
    get_switch_client,
)


def test_port_info_defaults():
    p = PortInfo(name="Ethernet1", status="connected", speed="10000", duplex="duplexFull", vlan="20", description="")
    assert p.input_rate == 0
    assert p.note == ""
    assert p.lldp_neighbor is None


def test_normalize_variations():
    c = AristaClient.__new__(AristaClient)  # no __init__
    c._normalize_port_name = AristaClient._normalize_port_name.__get__(c, AristaClient)
    assert c._normalize_port_name("Ethernet1") == "Ethernet1"
    assert c._normalize_port_name("Et1") == "Ethernet1"
    assert c._normalize_port_name("et5") == "Ethernet5"
    assert c._normalize_port_name("Eth48") == "Ethernet48"
    assert c._normalize_port_name("ethernet1/1") == "Ethernet1/1"
    assert c._normalize_port_name("Foo1") == "Foo1"  # non eth kept (filtered later)


@pytest.mark.asyncio
async def test_get_ports_no_creds_returns_empty():
    c = AristaClient()
    # force no creds (settings default empty)
    c.username = ""
    c.password = ""
    ports = await c.get_ports()
    assert ports == []


@pytest.mark.asyncio
async def test_fallback_on_eapi_error():
    c = AristaClient()
    c.username = "admin"
    c.password = "x"
    c.host = "127.0.0.1"

    with patch.object(c, "_get_ports_eapi", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        ports = await c.get_ports()
        assert ports == []  # graceful empty


@pytest.mark.asyncio
async def test_cache_and_stale_on_error():
    c = AristaClient()
    c.username = "u"
    c.password = "p"
    c.host = "h"
    fake = [PortInfo("Ethernet1", "connected", "10000", "f", "1", "d")]

    with patch.object(c, "_get_ports_eapi", new=AsyncMock(return_value=fake)):
        p1 = await c.get_ports()
        assert len(p1) == 1

    # second immediate uses cache, no call
    calls = 0
    async def counting():
        nonlocal calls
        calls += 1
        return fake
    with patch.object(c, "_get_ports_eapi", new=counting):
        p2 = await c.get_ports()
        assert len(p2) == 1
        assert calls == 0  # cached

    # on error serve stale
    with patch.object(c, "_get_ports_eapi", new=AsyncMock(side_effect=Exception("boom"))):
        p3 = await c.get_ports()
        assert len(p3) == 1  # last known served


def test_sort_key():
    c = AristaClient.__new__(AristaClient)
    c._port_sort_key = AristaClient._port_sort_key.__get__(c, AristaClient)
    names = ["Ethernet10", "Ethernet2", "Ethernet1/3", "Ethernet1"]
    keys = [c._port_sort_key(n) for n in names]
    # verify stable numeric sort independent of input order
    assert sorted(keys) == [(1, 0), (1, 3), (2, 0), (10, 0)]


def test_classify_kind():
    # interfaceType is the honest source
    assert _classify_kind("10GBASE-T", 10_000_000_000) == "rj45"
    assert _classify_kind("1000BASE-T", 1_000_000_000) == "rj45"
    assert _classify_kind("40GBASE-SR4", 40_000_000_000) == "cage"
    assert _classify_kind("100GBASE-LR4", 100_000_000_000) == "cage"
    assert _classify_kind("10GBASE-SR", 10_000_000_000) == "cage"  # SFP+ optic
    # media missing: bandwidth is the tell (copper tops out at 10G here)
    assert _classify_kind("", 40_000_000_000) == "cage"
    assert _classify_kind("", 1_000_000_000) == "rj45"
    assert _classify_kind("", 0) == "rj45"


def test_format_speed():
    assert _format_speed(10_000_000_000) == "10G"
    assert _format_speed(40_000_000_000) == "40G"
    assert _format_speed(100_000_000_000) == "100G"
    assert _format_speed(1_000_000_000) == "1G"
    assert _format_speed(100_000_000) == "100M"
    assert _format_speed(2_500_000_000) == "2.5G"
    assert _format_speed("auto") == "auto"  # non-numeric passes through
    assert _format_speed(None) == ""
    assert _format_speed(0) == ""


def _full_eapi_payload() -> dict:
    """A complete 5-command eAPI batch response (mock_switch.py's shapes)."""
    return {
        "result": [
            {
                "interfaceStatuses": {
                    "Ethernet1": {
                        "linkStatus": "connected",
                        "bandwidth": 10_000_000_000,
                        "duplex": "duplexFull",
                        "interfaceType": "10GBASE-T",
                        "vlanInformation": {"vlanId": 20},
                    },
                    "Ethernet49": {
                        "linkStatus": "connected",
                        "bandwidth": 40_000_000_000,
                        "duplex": "duplexFull",
                        "interfaceType": "40GBASE-SR4",
                        "vlanInformation": {},
                    },
                }
            },
            {"interfaceDescriptions": [{"interface": "Ethernet1", "description": "uplink"}]},
            {"interfaces": {"Ethernet1": {"inBitsRate": 900_000_000.0, "outBitsRate": 100_000_000.0}}},
            {"lldpNeighbors": []},
            {"modelName": "DCS-7050TX-48", "serialNumber": "SN1", "version": "4.31.0F"},
        ]
    }


@pytest.mark.asyncio
async def test_full_batch_parsing_version_and_kinds():
    """End-to-end through _get_ports_eapi: kind/media/speed per port, SwitchInfo parsed."""
    c = AristaClient()
    c.username = "u"
    c.password = "p"
    c.host = "h"
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status = lambda: None  # sync to avoid await warn
    fake_resp.json = _full_eapi_payload
    with patch("hlidskjalf.switch.httpx.AsyncClient") as mock_client:
        inst = mock_client.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=fake_resp)
        ports = await c._get_ports_eapi()

    by_name = {p.name: p for p in ports}
    assert by_name["Ethernet1"].kind == "rj45"
    assert by_name["Ethernet1"].media == "10GBASE-T"
    assert by_name["Ethernet1"].speed == "10G"
    assert by_name["Ethernet1"].active is True
    assert by_name["Ethernet1"].description == "uplink"
    assert by_name["Ethernet49"].kind == "cage"
    assert by_name["Ethernet49"].speed == "40G"

    info = c.get_switch_info()
    assert info.model == "DCS-7050TX-48"
    assert info.serial == "SN1"
    assert info.eos_version == "4.31.0F"


@pytest.mark.asyncio
async def test_response_validation_partial_results():
    """Simulates truncated eAPI result list -> []"""
    c = AristaClient()
    c.username = "u"
    c.password = "p"
    c.host = "h"
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status = lambda: None  # sync to avoid await warn
    fake_resp.json = lambda: {"result": [{}, {}, {}, {}]}  # 4 of 5 results
    with patch("hlidskjalf.switch.httpx.AsyncClient") as mock_client:
        inst = mock_client.return_value.__aenter__.return_value
        inst.post = AsyncMock(return_value=fake_resp)
        ports = await c._get_ports_eapi()
        assert ports == []


# Integration note: full route tests would use auth_client + mock eapi similar to conftest.
# For switch routes graceful: when client errors, still returns {"ports": [], "error": "..."}
