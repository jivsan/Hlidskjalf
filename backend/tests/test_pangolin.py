"""The optional Pangolin SSH-tunnel integration.

Proves the three behaviours that matter:

1. With the integration ENABLED, provisioning creates a TCP resource + a target
   (SSH, port 22) and stores the (vmid, resource_id, proxy_port) mapping; destroy
   deletes the resource.
2. Only ever an SSH/TCP resource — never a public HTTP one (http=false, protocol
   tcp, method tcp, target port 22).
3. With the integration UNCONFIGURED (a fresh clone), provisioning does nothing
   Pangolin-related and raises no error.

The Pangolin API is faked by dev/mock_pangolin.py running as a real uvicorn
subprocess, so the panel's httpx client exercises actual HTTP — the same shape as
the mock-PVE harness in conftest.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import csrf_headers

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "dev"


def _free_port() -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_pangolin():
    """A real mock-Pangolin server; yields its base URL."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_pangolin:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=DEV_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"mock_pangolin exited early: {proc.stderr.read().decode(errors='replace')}"
            )
        try:
            if httpx.get(f"{url}/_state", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            proc.terminate()
            raise RuntimeError("mock_pangolin did not become ready within 20s")
        time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def pangolin_enabled(mock_pangolin, monkeypatch):
    """Turn the integration on by pointing the live settings at the mock. All
    five knobs set -> Settings.pangolin_enabled is True. monkeypatch restores the
    (empty) defaults after the test, so other tests stay unconfigured."""
    from hlidskjalf.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "pangolin_api_url", mock_pangolin)
    monkeypatch.setattr(s, "pangolin_api_key", "test-pangolin-key")
    monkeypatch.setattr(s, "pangolin_org_id", "example-org")
    monkeypatch.setattr(s, "pangolin_site_id", 1)
    monkeypatch.setattr(s, "pangolin_ssh_port_start", 2200)
    assert s.pangolin_enabled
    return mock_pangolin


def _body(**overrides):
    body = {
        "name": "scratch-pangolin",
        "template_vmid": 9000,
        "cores": 2,
        "memory_mb": 2048,
        "disk_gb": 10,
        "vlan": "20",
        "ip_cidr": "192.168.20.221/24",
        "gateway": "192.168.20.1",
        "ssh_keys": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAATESTKEY test@test",
        "start": False,
    }
    body.update(overrides)
    return body


def test_provision_creates_ssh_tunnel_and_destroy_deletes_it(
    auth_client, pangolin_enabled
):
    state_url = f"{pangolin_enabled}/_state"

    r = auth_client.post("/api/vms", json=_body(), headers=csrf_headers(auth_client))
    assert r.status_code == 201, r.text
    vmid = r.json()["vmid"]

    note = r.json()["pangolin"]
    assert note["ssh_port"] >= 2200
    assert "warning" not in note
    resource_id = note["resource_id"]

    # The mock recorded exactly what the panel sent — SSH/TCP, never HTTP.
    state = httpx.get(state_url).json()
    res = next(x for x in state["resources"] if x["resourceId"] == resource_id)
    assert res["http"] is False              # GUARDRAIL: not a public HTTP resource
    assert res["protocol"] == "tcp"
    assert res["proxyPort"] == note["ssh_port"]

    tgt = state["targets"][str(resource_id)][0]
    assert tgt["ip"] == "192.168.20.221"     # the VM's static IP
    assert tgt["port"] == 22                  # SSH
    assert tgt["method"] == "tcp"
    assert tgt["siteId"] == 1

    # Destroy removes the resource and the stored mapping.
    d = auth_client.request(
        "DELETE", f"/api/vms/{vmid}",
        json={"confirm_name": "scratch-pangolin"}, headers=csrf_headers(auth_client),
    )
    assert d.status_code == 200, d.text
    assert d.json()["pangolin"]["deleted_resource_id"] == resource_id

    state = httpx.get(state_url).json()
    assert all(x["resourceId"] != resource_id for x in state["resources"])


def test_second_vm_gets_a_distinct_port(auth_client, pangolin_enabled):
    r1 = auth_client.post(
        "/api/vms", json=_body(name="scratch-pg-a", ip_cidr="192.168.20.222/24"),
        headers=csrf_headers(auth_client),
    )
    r2 = auth_client.post(
        "/api/vms", json=_body(name="scratch-pg-b", ip_cidr="192.168.20.223/24"),
        headers=csrf_headers(auth_client),
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["pangolin"]["ssh_port"] != r2.json()["pangolin"]["ssh_port"]


def test_provision_with_integration_unconfigured_does_nothing(auth_client):
    """A fresh clone (no pangolin_* set) provisions normally, with no Pangolin
    note and no error."""
    from hlidskjalf.config import get_settings

    assert not get_settings().pangolin_enabled
    r = auth_client.post(
        "/api/vms", json=_body(name="scratch-nopg", ip_cidr="192.168.20.224/24"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    assert "pangolin" not in r.json()
