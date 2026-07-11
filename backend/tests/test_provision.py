"""Provisioning: clone happy path (verified in the mock's config), validation."""

import urllib.parse

import httpx
from conftest import csrf_headers


def _body(**overrides):
    body = {
        "name": "scratch-test",
        "template_vmid": 9000,
        "cores": 2,
        "memory_mb": 2048,
        "disk_gb": 10,
        "vlan": "20",
        "ip_cidr": "10.0.20.201/24",
        "gateway": "10.0.20.1",
        "ssh_keys": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAATESTKEY test@test",
        "start": False,
    }
    body.update(overrides)
    return body


def test_create_vm_happy_path(auth_client, mock_pve_url):
    r = auth_client.post(
        "/api/vms", json=_body(), headers=csrf_headers(auth_client)
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]
    assert vmid >= 200
    assert r.json()["upids"]

    # verify what the panel actually wrote, straight from the mock's config
    cfg = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/hella/qemu/{vmid}/config"
    ).json()["data"]

    net0 = cfg["net0"]
    assert "firewall=0" in net0  # hard rule: every panel-written NIC
    assert "tag=20" in net0
    assert "bridge=vmbr0" in net0

    assert cfg["ipconfig0"] == "ip=10.0.20.201/24,gw=10.0.20.1"
    assert "ssh-ed25519" in urllib.parse.unquote(cfg["sshkeys"])
    assert str(cfg["cores"]) == "2"
    assert str(cfg["memory"]) == "2048"

    # disk was resized from the 4G template to 10G
    fleet = {v["vmid"]: v for v in auth_client.get("/api/vms").json()}
    assert fleet[vmid]["name"] == "scratch-test"
    assert fleet[vmid]["maxdisk"] == 10 << 30


def test_create_vm_duplicate_name_409(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="heimdall", ip_cidr="10.0.20.202/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 409


def test_create_vm_bad_vlan_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-badvlan", vlan="99"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "VLAN" in r.json()["detail"]


def test_create_vm_bad_ip_cidr_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-badip", ip_cidr="10.0.20.201"),  # missing /prefix
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "ip_cidr" in r.json()["detail"]


def test_create_vm_bad_name_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="Bad_Name!"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400


def test_create_vm_non_template_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-notpl", template_vmid=105),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "template" in r.json()["detail"]
