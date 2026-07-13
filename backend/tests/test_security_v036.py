"""Security guarantees added in v0.3.6.

Covers the findings from the audit:
- sessions are bound to the password they were issued under (a password change
  evicts every session issued before it)
- /api/tasks/{upid}/status is scoped to the guest the task belongs to (was an IDOR)
- login does not leak whether a username exists (timing-equalised)
- security headers are present on every response
"""

import time

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

OTHER_VMID = 115  # a normal mock vmid NOT assigned to the tenant below
OWNED_VMID = 130  # assigned to the tenant below


def login(client, username: str, password: str):
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    if r.status_code == 200:
        client.csrf = r.json()["csrf"]
    return r


@pytest.fixture
def tenant(client):
    """A regular user owning OWNED_VMID."""
    login(client, ADMIN_USER, ADMIN_PASSWORD)
    client.post(
        "/api/users",
        json={
            "username": "sec36-tenant",
            "password": "tenantpass1",
            "role": "user",
            "vmid": OWNED_VMID,
        },
        headers=csrf_headers(client),
    )
    yield "sec36-tenant"
    login(client, ADMIN_USER, ADMIN_PASSWORD)
    client.delete("/api/users/sec36-tenant", headers=csrf_headers(client))


# --- session is bound to the password it was issued under --------------------


def test_password_change_invalidates_existing_sessions(client, tenant):
    """The whole point of resetting a password is to evict whoever has the session."""
    login(client, tenant, "tenantpass1")
    assert client.get("/api/session").status_code == 200
    stolen = dict(client.cookies)

    # Admin resets the tenant's password (the recovery path).
    login(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.post(
        f"/api/users/{tenant}/password",
        json={"password": "rotated-pw-1"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 200

    # The session captured before the reset is now worthless.
    client.cookies.clear()
    for k, v in stolen.items():
        client.cookies.set(k, v)
    assert client.get("/api/session").status_code == 401


def test_session_still_valid_without_password_change(client, tenant):
    login(client, tenant, "tenantpass1")
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/session").status_code == 200


# --- /api/tasks/{upid}/status scoping (IDOR) ---------------------------------


def _upid(vmid: int) -> str:
    return f"UPID:pve:0000A1B2:00C3D4E5:{int(time.time()):08X}:qmstart:{vmid}:root@pam:"


def test_tenant_cannot_read_task_status_of_another_vm(client, tenant):
    login(client, tenant, "tenantpass1")
    r = client.get(f"/api/tasks/{_upid(OTHER_VMID)}/status")
    assert r.status_code == 403


def test_tenant_can_read_task_status_of_own_vm(client, tenant):
    login(client, tenant, "tenantpass1")
    r = client.get(f"/api/tasks/{_upid(OWNED_VMID)}/status")
    assert r.status_code != 403


def test_tenant_cannot_read_node_level_task_status(client, tenant):
    """A UPID with no numeric guest id is a node task — admins only."""
    login(client, tenant, "tenantpass1")
    upid = "UPID:pve:0000A1B2:00C3D4E5:66000000:srvstart:sshd:root@pam:"
    assert client.get(f"/api/tasks/{upid}/status").status_code == 403


def test_admin_can_read_any_task_status(client):
    login(client, ADMIN_USER, ADMIN_PASSWORD)
    assert client.get(f"/api/tasks/{_upid(OTHER_VMID)}/status").status_code != 403


# --- login does not leak whether a username exists ---------------------------


def test_login_same_error_for_unknown_user_and_bad_password(anon):
    unknown = anon.post(
        "/api/login", json={"username": "no-such-person", "password": "whatever1"}
    )
    bad_pw = anon.post(
        "/api/login", json={"username": ADMIN_USER, "password": "definitely-wrong"}
    )
    assert unknown.status_code == bad_pw.status_code == 401
    assert unknown.json()["detail"] == bad_pw.json()["detail"]


# --- security headers --------------------------------------------------------


def test_security_headers_present(anon):
    r = anon.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "default-src 'self'" in csp


def test_api_responses_are_not_cacheable(anon):
    assert anon.get("/api/health").headers["cache-control"] == "no-store"
