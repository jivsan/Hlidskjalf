"""Admin provisioning settings: GET/PUT roundtrip, validation, env-lock, and
that a PUT actually changes what provisioning writes (bridge + VLANs)."""

import httpx
import pytest
from conftest import csrf_headers


@pytest.fixture(autouse=True)
def _restore_provision_settings():
    """PUTs here mutate the session-scoped live Settings; put everything back."""
    from hlidskjalf.config import get_settings

    s = get_settings()
    snap = (dict(s.vlan_gateways), s.clone_storage, s.pve_bridge)
    yield
    object.__setattr__(s, "vlan_gateways", snap[0])
    object.__setattr__(s, "clone_storage", snap[1])
    object.__setattr__(s, "pve_bridge", snap[2])


@pytest.fixture()
def vlans_unlocked(monkeypatch):
    """Simulate a deployment that does NOT set HLIDSKJALF_VLAN_GATEWAYS.

    conftest sets it for the whole suite, which (correctly) env-locks the key.
    The route checks the live environment; apply_stored checks
    model_fields_set — undo both, restore on teardown.
    """
    from hlidskjalf.config import get_settings

    monkeypatch.delenv("HLIDSKJALF_VLAN_GATEWAYS", raising=False)
    s = get_settings()
    had = "vlan_gateways" in s.model_fields_set
    s.model_fields_set.discard("vlan_gateways")
    yield
    if had:
        s.model_fields_set.add("vlan_gateways")


def _put_body(**overrides):
    body = {
        "vlan_gateways": {"20": "192.168.20.1", "30": "", "50": "192.168.50.1"},
        "clone_storage": "local-lvm",
        "bridge": "vmbr0",
    }
    body.update(overrides)
    return body


# --- authorisation -----------------------------------------------------------


def test_get_requires_session(anon):
    assert anon.get("/api/settings/provision").status_code == 401


def test_non_admin_403(auth_client):
    r = auth_client.post(
        "/api/users",
        json={"username": "tenant-settings", "password": "password123", "vmid": 115},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    r = auth_client.post(
        "/api/login", json={"username": "tenant-settings", "password": "password123"}
    )
    assert r.status_code == 200
    csrf = r.json()["csrf"]

    assert auth_client.get("/api/settings/provision").status_code == 403
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(),
        headers={"X-Hlidskjalf-CSRF": csrf},
    )
    assert r.status_code == 403


# --- GET ----------------------------------------------------------------------


def test_get_defaults_and_options(auth_client):
    r = auth_client.get("/api/settings/provision")
    assert r.status_code == 200, r.text
    d = r.json()
    # values as configured by conftest / defaults
    assert d["vlan_gateways"] == {"20": "192.168.20.1", "30": "", "50": "192.168.50.1"}
    assert d["clone_storage"] == "local-lvm"
    assert d["bridge"] == "vmbr0"
    # options straight from the mock node
    assert d["options"]["storages"] == ["local-lvm", "vm-store"]  # images-capable only
    assert d["options"]["bridges"] == ["vmbr0", "vmbr1"]
    # conftest supplies HLIDSKJALF_VLAN_GATEWAYS, nothing else
    assert d["env_locked"] == ["vlan_gateways"]


# --- PUT ----------------------------------------------------------------------


def test_put_roundtrip(auth_client, vlans_unlocked):
    body = _put_body(
        vlan_gateways={"20": "192.168.20.1", "40": "192.168.40.1"},
        clone_storage="vm-store",
        bridge="vmbr1",
    )
    r = auth_client.put(
        "/api/settings/provision", json=body, headers=csrf_headers(auth_client)
    )
    assert r.status_code == 200, r.text

    d = auth_client.get("/api/settings/provision").json()
    assert d["vlan_gateways"] == {"20": "192.168.20.1", "40": "192.168.40.1"}
    assert d["clone_storage"] == "vm-store"
    assert d["bridge"] == "vmbr1"
    assert d["env_locked"] == []

    # persisted, not just applied: the config table holds the new values
    import sqlite3

    from hlidskjalf.config import get_settings

    with sqlite3.connect(get_settings().db_path) as con:
        stored = dict(con.execute("SELECT key, value FROM config"))
    assert stored["pve_bridge"] == "vmbr1"
    assert stored["clone_storage"] == "vm-store"
    assert '"40"' in stored["vlan_gateways"]


@pytest.mark.parametrize(
    "gateways",
    [
        {"abc": "192.168.20.1"},  # non-numeric tag
        {"0": "192.168.20.1"},  # below range
        {"4095": "192.168.20.1"},  # above range
        {"20": "not-an-ip"},  # bad gateway
        {"20": "192.168.20"},  # truncated gateway
    ],
)
def test_put_bad_vlan_rows_400(auth_client, vlans_unlocked, gateways):
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(vlan_gateways=gateways),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400, r.text


def test_put_unknown_storage_or_bridge_400(auth_client, vlans_unlocked):
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(clone_storage="does-not-exist"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "storage" in r.json()["detail"]

    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(bridge="vmbr9"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "bridge" in r.json()["detail"]


def test_put_env_locked_key_refused(auth_client, monkeypatch):
    # conftest already locks vlan_gateways via the environment
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(vlan_gateways={"20": "192.168.20.1"}),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "environment" in r.json()["detail"]
    assert "HLIDSKJALF_VLAN_GATEWAYS" in r.json()["detail"]

    # lock another key and try to change it
    monkeypatch.setenv("HLIDSKJALF_PVE_BRIDGE", "vmbr0")
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(bridge="vmbr1"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "HLIDSKJALF_PVE_BRIDGE" in r.json()["detail"]

    # an env-locked key submitted UNCHANGED is fine (the form sends all keys)
    r = auth_client.put(
        "/api/settings/provision", json=_put_body(), headers=csrf_headers(auth_client)
    )
    assert r.status_code == 200, r.text


# --- the settings actually steer provisioning ---------------------------------


def test_provision_uses_configured_bridge_and_vlan(
    auth_client, vlans_unlocked, mock_pve_url
):
    r = auth_client.put(
        "/api/settings/provision",
        json=_put_body(
            vlan_gateways={"40": "192.168.40.1"}, clone_storage="vm-store", bridge="vmbr1"
        ),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/vms",
        json={
            "name": "scratch-settings",
            "template_vmid": 9000,
            "cores": 1,
            "memory_mb": 1024,
            "disk_gb": 8,
            "vlan": "40",
            "ip_cidr": "192.168.40.201/24",
            "gateway": "192.168.40.1",
            "start": False,
        },
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]

    cfg = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{vmid}/config"
    ).json()["data"]
    net0 = cfg["net0"]
    assert "bridge=vmbr1" in net0
    assert "tag=40" in net0
    assert "firewall=0" in net0  # hard rule, regardless of bridge
