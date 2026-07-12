"""User management authorization + robustness (routes/users.py).

Locks in the multi-user model's admin-only guards, the one-VM-per-user 409s,
password validation, and the robustness fixes (existence -> 404, last-admin
protection). The shared harness (conftest.py) is imported read-only.

Note on shared state: `client` is session-scoped, so the users table (a single
sqlite in the session STATE_DIR) accumulates across tests. Every helper below
therefore uses unique usernames and cleans up the users it creates so tests
stay order-independent (in particular, the bootstrap admin `christina` must
remain the *only* admin for the last-admin test to be meaningful).
"""

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers


def login_as(client, username: str, password: str) -> dict:
    """Clear the shared cookie jar and log in; stash CSRF on `.csrf`."""
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.csrf = r.json()["csrf"]
    return r.json()


@pytest.fixture
def user_factory(auth_client):
    """Create users as the bootstrap admin; delete them all on teardown.

    Only 201 creations are tracked for cleanup, so it is safe to call with
    inputs expected to fail (409/422). Depends on `auth_client`, guaranteeing an
    admin session (and admin `.csrf`) is active when `create` is first called.
    """
    created: list[str] = []

    def create(username: str, password: str = "secretpw", role: str = "user", vmid=None):
        body = {"username": username, "password": password, "role": role}
        if vmid is not None:
            body["vmid"] = vmid
        r = auth_client.post("/api/users", json=body, headers=csrf_headers(auth_client))
        if r.status_code == 201:
            created.append(username)
        return r

    yield create

    # Re-establish an admin session (the test body may have switched identity).
    login_as(auth_client, ADMIN_USER, ADMIN_PASSWORD)
    for u in created:
        auth_client.delete(f"/api/users/{u}", headers=csrf_headers(auth_client))


# --- admin-only guards -------------------------------------------------------


def test_non_admin_forbidden_on_user_management(auth_client, user_factory):
    """A regular user is 403 on every user-management endpoint (CSRF supplied,
    so the 403 comes from the role guard, not the CSRF check)."""
    assert user_factory("nonadmin-actor").status_code == 201
    login_as(auth_client, "nonadmin-actor", "secretpw")
    h = csrf_headers(auth_client)

    assert auth_client.get("/api/users").status_code == 403
    assert auth_client.post(
        "/api/users",
        json={"username": "would-be", "password": "secretpw", "role": "user"},
        headers=h,
    ).status_code == 403
    assert auth_client.post(
        "/api/users/whoever/assign", json={"vmid": 120}, headers=h
    ).status_code == 403
    assert auth_client.delete("/api/users/whoever", headers=h).status_code == 403


# --- uniqueness / one-VM-per-user 409s ---------------------------------------


def test_duplicate_username_409(auth_client, user_factory):
    assert user_factory("dupe-name").status_code == 201
    assert user_factory("dupe-name").status_code == 409


def test_duplicate_vmid_at_create_409(auth_client, user_factory):
    assert user_factory("vm-owner-1", vmid=120).status_code == 201
    r = user_factory("vm-owner-2", vmid=120)
    assert r.status_code == 409
    assert "120" in r.json()["detail"]


def test_duplicate_vmid_at_assign_409(auth_client, user_factory):
    assert user_factory("assign-owner", vmid=130).status_code == 201
    assert user_factory("assign-other").status_code == 201
    r = auth_client.post(
        "/api/users/assign-other/assign",
        json={"vmid": 130},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 409


# --- password validation -----------------------------------------------------


def test_short_password_422(auth_client):
    """< 6 chars is rejected by the pydantic model before any handler logic."""
    r = auth_client.post(
        "/api/users",
        json={"username": "shortpw-user", "password": "short", "role": "user"},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 422


def test_user_can_change_own_password(auth_client, user_factory):
    assert user_factory("selfpw-user", password="origpass1").status_code == 201
    login_as(auth_client, "selfpw-user", "origpass1")
    r = auth_client.post(
        "/api/users/selfpw-user/password",
        json={"password": "newpass123"},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # The new password actually works.
    login_as(auth_client, "selfpw-user", "newpass123")


# --- robustness: non-existent target -> 404 ----------------------------------


def test_set_password_nonexistent_target_404(auth_client):
    r = auth_client.post(
        "/api/users/ghost-user/password",
        json={"password": "secretpw"},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 404


def test_assign_nonexistent_target_404(auth_client):
    r = auth_client.post(
        "/api/users/ghost-user/assign",
        json={"vmid": None},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 404


def test_delete_nonexistent_target_404(auth_client):
    r = auth_client.delete(
        "/api/users/ghost-user", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 404


# --- self-delete and last-admin guards ---------------------------------------


def test_self_delete_forbidden(auth_client, user_factory):
    """With a second admin present, the sole-admin guard does not fire, so a
    self-delete falls through to the 'cannot delete yourself' guard."""
    assert user_factory("admin-b", role="admin").status_code == 201
    r = auth_client.delete(
        f"/api/users/{ADMIN_USER}", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"].lower()


def test_delete_last_admin_forbidden(auth_client):
    """The bootstrap admin is the only admin in a clean suite; deleting them
    hits the last-admin guard (which precedes the self-delete guard)."""
    r = auth_client.delete(
        f"/api/users/{ADMIN_USER}", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 400
    assert "last admin" in r.json()["detail"].lower()
    # The admin must still be present afterwards.
    users = auth_client.get("/api/users").json()
    assert any(
        u["username"] == ADMIN_USER and u["role"] == "admin" for u in users
    )
