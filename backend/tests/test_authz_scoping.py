"""Per-VM authorization scoping for regular (VPS-customer) users.

Asserts the model already enforced on `main`: a `user` assigned to exactly one
vmid may only ever see/act on that vmid, and admin-only endpoints are closed to
them. Console/rescue scoping is deliberately NOT covered here (those are being
fixed on a separate branch and are not yet scoped on `main`).

The shared harness (conftest.py) is imported read-only.
"""

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

SCOPED_USER = "scope-cust"
SCOPED_PW = "scopepw123"
VMID_A = 105  # normal mock vmid, owned by the scoped user
VMID_B = 115  # normal mock vmid, NOT owned by the scoped user


def login_as(client, username: str, password: str) -> dict:
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.csrf = r.json()["csrf"]
    return r.json()


def _admin_csrf(client) -> str:
    login_as(client, ADMIN_USER, ADMIN_PASSWORD)
    return client.csrf


@pytest.fixture(scope="module")
def scoped_user(client):
    """Create (as admin) a regular user assigned to VMID_A; delete on teardown."""
    csrf = _admin_csrf(client)
    r = client.post(
        "/api/users",
        json={
            "username": SCOPED_USER,
            "password": SCOPED_PW,
            "role": "user",
            "vmid": VMID_A,
        },
        headers={"X-Hlidskjalf-CSRF": csrf},
    )
    assert r.status_code == 201, r.text
    yield SCOPED_USER
    csrf = _admin_csrf(client)
    client.delete(f"/api/users/{SCOPED_USER}", headers={"X-Hlidskjalf-CSRF": csrf})


# --- fleet + detail scoping --------------------------------------------------


def test_user_lists_only_their_vm(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    r = client.get("/api/vms")
    assert r.status_code == 200
    assert [v["vmid"] for v in r.json()] == [VMID_A]


def test_user_reads_own_vm_detail(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    assert client.get(f"/api/vms/{VMID_A}").status_code == 200


def test_user_cannot_read_other_vm_detail(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    assert client.get(f"/api/vms/{VMID_B}").status_code == 403


# --- power action scoping ----------------------------------------------------


def test_user_cannot_power_other_vm(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    r = client.post(
        f"/api/vms/{VMID_B}/status/start", headers=csrf_headers(client)
    )
    assert r.status_code == 403


def test_user_power_own_vm_not_forbidden(scoped_user, client):
    """Ownership must not block acting on the user's own VM (it may fail for
    other reasons, but never 403 on ownership)."""
    login_as(client, SCOPED_USER, SCOPED_PW)
    r = client.post(
        f"/api/vms/{VMID_A}/status/start", headers=csrf_headers(client)
    )
    assert r.status_code != 403


# --- bandwidth + metrics scoping ---------------------------------------------


def test_user_cannot_read_other_vm_bandwidth(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    assert client.get(f"/api/vms/{VMID_B}/bandwidth").status_code == 403


def test_user_cannot_read_other_vm_metrics(scoped_user, client):
    login_as(client, SCOPED_USER, SCOPED_PW)
    assert client.get(f"/api/vms/{VMID_B}/metrics").status_code == 403


# --- admin-only endpoints closed to regular users ----------------------------

ADMIN_ONLY_GETS = [
    "/api/node",
    "/api/node/metrics",
    "/api/bandwidth/summary",
    "/api/templates",
    "/api/provision/defaults",
    "/api/tasks/recent",
    "/api/debug/health",
]


@pytest.mark.parametrize("path", ADMIN_ONLY_GETS)
def test_admin_only_endpoint_forbidden_for_user(scoped_user, client, path):
    login_as(client, SCOPED_USER, SCOPED_PW)
    assert client.get(path).status_code == 403, path
