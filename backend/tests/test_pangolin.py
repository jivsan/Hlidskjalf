"""The optional Pangolin SSH-tunnel integration.

Proves the behaviours that matter:

1. With the integration ENABLED, provisioning creates a TCP resource + a target
   (SSH, port 22) and stores the (vmid, resource_id, proxy_port) mapping; destroy
   deletes the resource.
2. Only ever an SSH/TCP resource — never a public HTTP one (http=false, protocol
   tcp, method tcp, target port 22).
3. With the integration UNCONFIGURED (a fresh clone), provisioning does nothing
   Pangolin-related and raises no error.
4. Lifecycle hardening (security audit): port reservation is race-free, a destroy
   of a VM already gone from PVE still cleans the tunnel up, a failed delete is
   carried across a reprovision and retried, a failure after the create still
   leaves the resource id recorded, and the API URL must be https.

The Pangolin API is faked by dev/mock_pangolin.py running as a real uvicorn
subprocess, so the panel's httpx client exercises actual HTTP — the same shape as
the mock-PVE harness in conftest.
"""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiosqlite
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


# --- lifecycle hardening (security audit) --------------------------------------


@pytest.fixture
async def pangodb(tmp_path):
    """A private Db (own file, own event loop) for the row-level tests — the
    app's app.state.db belongs to the TestClient's portal loop."""
    from hlidskjalf.db import Db

    db = Db(tmp_path / "hlidskjalf.sqlite3")
    await db.open()
    yield db
    await db.close()


async def test_concurrent_port_reservations_never_collide(pangodb):
    """The old order — scan the pool, create the resource, insert the row — let
    two concurrent provisions take the SAME port. Reservation is now atomic."""
    ports = await asyncio.gather(
        *(pangodb.pangolin_reserve_port(400 + i, 2200) for i in range(24))
    )
    assert sorted(ports) == list(range(2200, 2224))


async def test_proxy_port_has_a_unique_index_backstop(pangodb):
    """Even if a race ever slipped past the lock, the schema itself refuses two
    rows on one port."""
    await pangodb.pangolin_reserve_port(401, 2200)
    with pytest.raises(aiosqlite.IntegrityError):
        await pangodb.conn.execute(
            "INSERT INTO pangolin_resources (vmid, resource_id, proxy_port) "
            "VALUES (402, 5, 2200)"
        )
    await pangodb.conn.rollback()


async def test_reprovision_reservation_carries_the_live_id_into_orphan_debts(pangodb):
    """A failed delete keeps the row; a reprovision must not overwrite it — the
    old resource id moves into orphan_ids so a later retry still finds it."""
    port = await pangodb.pangolin_reserve_port(410, 2200)
    await pangodb.pangolin_set_resource(410, 1001)
    again = await pangodb.pangolin_reserve_port(410, 2200)
    assert again == port  # the VMID keeps its port
    row = await pangodb.pangolin_get(410)
    assert row["resource_id"] is None
    assert row["orphan_ids"] == [1001]
    # and that port stays reserved — it is not handed to another VM while the
    # row tracks debts
    assert await pangodb.pangolin_reserve_port(411, 2200) == port + 1


async def test_release_port_drops_a_bare_reservation_but_keeps_orphan_debts(pangodb):
    port = await pangodb.pangolin_reserve_port(420, 2200)
    await pangodb.pangolin_release_port(420)  # create failed: hand the port back
    assert await pangodb.pangolin_get(420) is None
    assert await pangodb.pangolin_reserve_port(421, 2200) == port  # reusable

    # ...but a row still owed a delete survives the release
    await pangodb.pangolin_set_resource(421, 1002)
    await pangodb.pangolin_reserve_port(421, 2200)  # 1002 -> orphan_ids
    await pangodb.pangolin_release_port(421)
    row = await pangodb.pangolin_get(421)
    assert row is not None and row["orphan_ids"] == [1002]


def test_destroy_of_vm_gone_from_pve_still_cleans_up_the_tunnel(
    auth_client, pangolin_enabled, mock_pve_url
):
    """The VM was deleted out-of-band (the Proxmox UI, say). 404ing used to
    strand the tunnel resource with no panel path to remove it; the destroy now
    runs the cleanup and reports the VM as already gone."""
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-pg-oob", ip_cidr="192.168.20.232/24", vmid=332),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    resource_id = r.json()["pangolin"]["resource_id"]

    # Out-of-band delete: straight against Proxmox, bypassing the panel.
    assert httpx.delete(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/332"
    ).status_code == 200

    d = auth_client.request(
        "DELETE", "/api/vms/332",
        json={"confirm_name": "irrelevant-vm-is-gone"},
        headers=csrf_headers(auth_client),
    )
    assert d.status_code == 200, d.text
    assert d.json()["already_gone"] is True
    assert d.json()["pangolin"]["deleted_resource_id"] == resource_id

    state = httpx.get(f"{pangolin_enabled}/_state").json()
    assert all(x["resourceId"] != resource_id for x in state["resources"])


def test_failed_delete_survives_reprovision_and_the_retry_deletes_both(
    auth_client, pangolin_enabled
):
    """A Pangolin delete fails -> the row is kept. A reprovision must not lose
    the orphan's id, and the next destroy must attempt BOTH deletes."""
    r1 = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-pg-orphan", ip_cidr="192.168.20.230/24", vmid=330),
        headers=csrf_headers(auth_client),
    )
    assert r1.status_code == 201, r1.text
    note1 = r1.json()["pangolin"]
    orphan_id, port = note1["resource_id"], note1["ssh_port"]

    # The first delete attempt fails (Pangolin outage) — the VM still destroys.
    httpx.post(f"{pangolin_enabled}/_fail_next/delete")
    d1 = auth_client.request(
        "DELETE", "/api/vms/330",
        json={"confirm_name": "scratch-pg-orphan"},
        headers=csrf_headers(auth_client),
    )
    assert d1.status_code == 200, d1.text
    assert "warning" in d1.json()["pangolin"]
    state = httpx.get(f"{pangolin_enabled}/_state").json()
    assert any(x["resourceId"] == orphan_id for x in state["resources"])

    # Reprovision the same VMID: the orphan id must be carried, not overwritten.
    r2 = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-pg-orphan", ip_cidr="192.168.20.230/24", vmid=330),
        headers=csrf_headers(auth_client),
    )
    assert r2.status_code == 201, r2.text
    note2 = r2.json()["pangolin"]
    assert "warning" not in note2
    new_id = note2["resource_id"]
    assert new_id != orphan_id
    assert note2["ssh_port"] == port  # the VMID kept its port

    # The next destroy retries EVERYTHING owed: live resource and orphan alike.
    d2 = auth_client.request(
        "DELETE", "/api/vms/330",
        json={"confirm_name": "scratch-pg-orphan"},
        headers=csrf_headers(auth_client),
    )
    assert d2.status_code == 200, d2.text
    deleted = d2.json()["pangolin"]["deleted_resource_ids"]
    assert set(deleted) == {orphan_id, new_id}
    state = httpx.get(f"{pangolin_enabled}/_state").json()
    assert all(
        x["resourceId"] not in (orphan_id, new_id) for x in state["resources"]
    )


def test_target_attach_failure_still_records_the_resource_id(
    auth_client, pangolin_enabled
):
    """A failure AFTER the Pangolin create (attaching the target) must not
    strand an untracked resource: the id is recorded immediately, so a later
    destroy can clean it up."""
    httpx.post(f"{pangolin_enabled}/_fail_next/target")
    r = auth_client.post(
        "/api/vms",
        json=_body(name="scratch-pg-target", ip_cidr="192.168.20.231/24", vmid=331),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text  # best-effort: the VM create still wins
    assert "warning" in r.json()["pangolin"]

    # The resource exists in Pangolin even though wiring it up failed.
    state = httpx.get(f"{pangolin_enabled}/_state").json()
    orphaned = next(
        x for x in state["resources"] if x["name"] == "scratch-pg-target"
    )

    # The id WAS recorded: the destroy finds it and cleans the resource up.
    d = auth_client.request(
        "DELETE", "/api/vms/331",
        json={"confirm_name": "scratch-pg-target"},
        headers=csrf_headers(auth_client),
    )
    assert d.status_code == 200, d.text
    assert d.json()["pangolin"]["deleted_resource_id"] == orphaned["resourceId"]
    state = httpx.get(f"{pangolin_enabled}/_state").json()
    assert all(x["resourceId"] != orphaned["resourceId"] for x in state["resources"])


# --- the API URL must be TLS -----------------------------------------------------


def test_pangolin_api_url_must_be_https():
    """The org-scoped bearer key rides to this URL — plain http to a routable
    host would send it cleartext. Refused at settings load."""
    from pydantic import ValidationError

    from hlidskjalf.config import Settings

    with pytest.raises(ValidationError):
        Settings(pangolin_api_url="http://pangolin-api.example.com/v1")
    # a schemeless URL is not https either
    with pytest.raises(ValidationError):
        Settings(pangolin_api_url="pangolin-api.example.com/v1")

    s = Settings(pangolin_api_url="https://pangolin-api.example.com/v1")
    assert s.pangolin_api_url == "https://pangolin-api.example.com/v1"

    # http is tolerated only for loopback (a local mock never leaves the host)
    for url in (
        "http://127.0.0.1:18443/v1",
        "http://localhost:18443",
        "http://[::1]:18443",
    ):
        Settings(pangolin_api_url=url)
    # and empty stays legal — the integration is simply off
    Settings(pangolin_api_url="")


def test_pangolin_api_url_from_env_is_rejected_too(monkeypatch):
    from pydantic import ValidationError

    from hlidskjalf.config import Settings

    monkeypatch.setenv(
        "HLIDSKJALF_PANGOLIN_API_URL", "http://pangolin-api.example.com"
    )
    with pytest.raises(ValidationError):
        Settings()
