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
        "ip_cidr": "192.168.20.201/24",
        "gateway": "192.168.20.1",
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
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{vmid}/config"
    ).json()["data"]

    net0 = cfg["net0"]
    assert "firewall=0" in net0  # hard rule: every panel-written NIC
    assert "tag=20" in net0
    assert "bridge=vmbr0" in net0

    assert cfg["ipconfig0"] == "ip=192.168.20.201/24,gw=192.168.20.1"
    assert "ssh-ed25519" in urllib.parse.unquote(cfg["sshkeys"])
    assert str(cfg["cores"]) == "2"
    assert str(cfg["memory"]) == "2048"

    # disk was resized from the 4G template to 10G
    fleet = {v["vmid"]: v for v in auth_client.get("/api/vms").json()}
    assert fleet[vmid]["name"] == "scratch-test"
    assert fleet[vmid]["maxdisk"] == 10 << 30


# --- default nameserver (a DMZ that permits DNS to only one resolver) --------
# Per-deployment, never hardcoded: empty default leaves cloud-init's nameserver
# untouched; a configured value is written into every provisioned VM.


def test_create_vm_no_nameserver_by_default(auth_client, mock_pve_url):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-nodns", ip_cidr="192.168.20.210/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]
    cfg = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{vmid}/config"
    ).json()["data"]
    assert "nameserver" not in cfg


def test_create_vm_sets_default_nameserver(auth_client, mock_pve_url, monkeypatch):
    from hlidskjalf.config import get_settings

    monkeypatch.setattr(get_settings(), "default_nameserver", "9.9.9.9")
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-dns", ip_cidr="192.168.20.211/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]
    cfg = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{vmid}/config"
    ).json()["data"]
    assert cfg["nameserver"] == "9.9.9.9"


def test_create_vm_duplicate_name_409(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="panel-host", ip_cidr="192.168.20.202/24"),
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
        json=_body(name="scratch-badip", ip_cidr="192.168.20.201"),  # missing /prefix
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


# --- cloud-init login credentials -------------------------------------------
# A cloud image sets no password of its own: cloud-init is the only thing that
# gives the guest a way in. The panel used to write ciuser + sshkeys but never a
# password, so a VM created without an SSH key you hold was unloginnable — it
# looked exactly like "username or password is wrong" at the console.


def test_create_vm_sets_login_user_and_password(auth_client, mock_pve_url):
    secret = "corr3ct-horse"
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-creds", ip_cidr="192.168.20.250/24",
                   ci_user="tenant", ci_password=secret),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]
    cfg = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/{vmid}/config"
    ).json()["data"]
    assert cfg["ciuser"] == "tenant"
    assert cfg["cipassword"] == secret
    # the panel must never echo the password back to the caller
    assert secret not in r.text


def test_create_vm_password_never_reaches_the_audit_log(auth_client):
    secret = "sup3rsecret-pw"
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-audit", ip_cidr="192.168.20.251/24",
                   ci_password=secret),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    audit = auth_client.get("/api/debug/audit").json()
    assert secret not in str(audit)


def test_create_vm_with_no_password_and_no_key_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-noway", ip_cidr="192.168.20.252/24", ssh_keys=""),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "no way to log in" in r.json()["detail"]


def test_create_vm_with_password_but_no_key_ok(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-pwonly", ip_cidr="192.168.20.253/24",
                   ssh_keys="", ci_password="letmein-please"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text


def test_create_vm_bad_login_user_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-baduser", ci_user="1nvalid Name"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "username" in r.json()["detail"]


def test_create_vm_short_password_400(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-shortpw", ci_password="short"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "8 characters" in r.json()["detail"]


# --- choosing the VMID yourself ---------------------------------------------
# A clone writes to whatever newid it is given. If the panel ever passed a VMID
# that is already in use, or a protected one, Proxmox would refuse (or worse) —
# so the refusals below are the whole point of letting anyone type a number.


def test_create_vm_with_chosen_vmid(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-chosen", vmid=942, ip_cidr="192.168.20.242/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    assert r.json()["vmid"] == 942

    fleet = {v["vmid"]: v for v in auth_client.get("/api/vms").json()}
    assert fleet[942]["name"] == "scratch-chosen"


def test_create_vm_omitting_vmid_still_takes_the_next_free_one(auth_client):
    used = set(auth_client.get("/api/provision/defaults").json()["used_vmids"])
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-auto", ip_cidr="192.168.20.243/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]
    assert vmid >= 200 and vmid not in used


def test_create_vm_with_taken_vmid_409(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-taken", vmid=105, ip_cidr="192.168.20.244/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 409
    assert "105" in r.json()["detail"]


def test_create_vm_with_protected_vmid_403(auth_client):
    """151 is in HLIDSKJALF_PROTECTED_VMIDS — cloning onto it would displace it."""
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-protected", vmid=151, ip_cidr="192.168.20.245/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 403
    assert "protected" in r.json()["detail"]


def test_create_vm_with_vmid_below_proxmox_floor_422(auth_client):
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-lowid", vmid=99, ip_cidr="192.168.20.246/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 422


def test_defaults_reports_used_and_protected_vmids(auth_client):
    d = auth_client.get("/api/provision/defaults").json()
    assert 105 in d["used_vmids"]
    assert d["protected_vmids"] == [101, 151]
    assert d["next_vmid"] not in d["used_vmids"]
    assert d["min_vmid"] == 100
