"""Rescue mode enter/exit: boot order swap, ISO attach/detach, panel flag."""

import httpx
from conftest import csrf_headers

VMID = 140  # scratch-old
ISO = "local:iso/systemrescue-12.01-amd64.iso"


def _mock_config(mock_pve_url):
    return httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{VMID}/config"
    ).json()["data"]


def test_enter_rescue(auth_client, mock_pve_url):
    r = auth_client.post(
        f"/api/vms/{VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rescue"] is True
    slot = body["slot"]
    # ide2 carries cloud-init in the mock config, so a free ide slot is picked
    assert slot in ("ide0", "ide1", "ide3")
    assert body["upids"]

    cfg = _mock_config(mock_pve_url)
    assert cfg["boot"] == f"order={slot}"
    assert cfg[slot] == f"{ISO},media=cdrom"

    # panel db flags the VM as rescued
    detail = auth_client.get(f"/api/vms/{VMID}").json()
    assert detail["rescue"] is True
    assert detail["rescue_since"]
    fleet = {v["vmid"]: v for v in auth_client.get("/api/vms").json()}
    assert fleet[VMID]["rescue"] is True


def test_enter_rescue_twice_409(auth_client):
    r = auth_client.post(
        f"/api/vms/{VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 409
    assert "already in rescue" in r.json()["detail"]


def test_exit_rescue(auth_client, mock_pve_url):
    before = _mock_config(mock_pve_url)
    slot = before["boot"].removeprefix("order=")  # the borrowed rescue slot

    r = auth_client.request(
        "DELETE", f"/api/vms/{VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 200, r.text
    assert r.json()["rescue"] is False

    cfg = _mock_config(mock_pve_url)
    assert cfg["boot"] == "order=scsi0"  # original boot order restored
    assert slot not in cfg  # borrowed ide slot cleared

    detail = auth_client.get(f"/api/vms/{VMID}").json()
    assert detail["rescue"] is False
    assert detail["rescue_since"] is None


def test_exit_rescue_when_not_in_rescue_409(auth_client):
    r = auth_client.request(
        "DELETE", f"/api/vms/{VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 409
    assert "not in rescue" in r.json()["detail"]
