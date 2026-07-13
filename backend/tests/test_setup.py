"""First-run setup wizard.

The security property under test: setup is reachable IFF no user exists. These
endpoints are unauthenticated by necessity, so if they stayed open after an admin
existed they would be an unauthenticated takeover backdoor.

The shared session-scoped `client` fixture already has a seeded admin, so setup is
CLOSED there — that is what the lockout tests assert. The open-setup case gets its
own app against an empty DB.
"""

import os
import tempfile

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, MOCK_PORT, csrf_headers
from fastapi.testclient import TestClient


def _pve_conn() -> dict:
    """A connection pointing at the mock PVE (plain http, so no fingerprint)."""
    return {
        "host": "127.0.0.1",
        "port": MOCK_PORT,
        "node": "hella",
        "scheme": "http",
        "token_id": "hlidskjalf@pve!panel",
        "token_secret": "mock-secret",
        "fingerprint": "",
        "verify_tls": True,
    }


@pytest.fixture
def fresh_app(client, monkeypatch):
    """An app instance with an EMPTY database — i.e. setup is still needed.

    Built by clearing the env that would otherwise seed a bootstrap admin, then
    re-importing the app so its lifespan runs against the fresh state dir.

    Depends on the session-scoped `client` purely for ordering: that fixture must
    construct and start the shared app BEFORE we reload the module out from under
    it, or the shared app would come up against this fixture's temp state dir (and
    then the "setup is closed" tests would be testing an empty database).
    """
    import importlib

    state = tempfile.mkdtemp(prefix="hlidskjalf-setup-test-")
    monkeypatch.setenv("HLIDSKJALF_STATE_DIR", state)
    monkeypatch.delenv("HLIDSKJALF_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("HLIDSKJALF_PVE_HOST", "")  # unconfigured: no PVE yet
    monkeypatch.setenv("HLIDSKJALF_SESSION_SECRET", "0123456789abcdef" * 4)

    from hlidskjalf import config

    config.get_settings.cache_clear()
    import hlidskjalf.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c

    config.get_settings.cache_clear()
    importlib.reload(main_mod)


# --- the gate ----------------------------------------------------------------


def test_setup_needed_on_a_fresh_install(fresh_app):
    r = fresh_app.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["needed"] is True


def test_setup_not_needed_once_a_user_exists(anon):
    """The shared app has a seeded admin — setup must report itself done."""
    r = anon.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["needed"] is False


def test_setup_commit_is_closed_once_a_user_exists(anon):
    """The backdoor test: POST /api/setup must be dead on a configured panel."""
    r = anon.post(
        "/api/setup",
        json={
            "pve": _pve_conn(),
            "admin": {"username": "intruder", "password": "intruderpw1"},
        },
    )
    assert r.status_code == 409


def test_setup_test_endpoint_is_closed_once_a_user_exists(anon):
    assert anon.post("/api/setup/test", json=_pve_conn()).status_code == 409


def test_intruder_was_not_created(anon):
    """Belt and braces: the refused setup must not have created anything."""
    login = anon.post("/api/login", json={"username": "intruder", "password": "intruderpw1"})
    assert login.status_code == 401


# --- an unconfigured panel is still usable enough to be configured -----------


def test_unconfigured_panel_boots_and_refuses_api_calls(fresh_app):
    """It must serve the wizard, not crash — but expose no Proxmox data.

    (PveClient refuses https without a pinned fingerprint, so before this fix a
    fresh install crashed on startup and could never be configured at all.)
    """
    assert fresh_app.get("/api/health").json() == {"ok": True}
    # 401 (nobody is logged in — no users exist) or 503 (no Proxmox configured);
    # either way, no data.
    assert fresh_app.get("/api/vms").status_code in (401, 503)


# --- connection validation ---------------------------------------------------


def test_setup_test_rejects_an_unreachable_host(fresh_app):
    conn = _pve_conn() | {"host": "127.0.0.1", "port": 9}  # discard port
    r = fresh_app.post("/api/setup/test", json=conn)
    assert r.status_code == 400
    assert "reach" in r.json()["detail"].lower()


def test_setup_test_rejects_an_unknown_node(fresh_app):
    r = fresh_app.post("/api/setup/test", json=_pve_conn() | {"node": "not-a-node"})
    assert r.status_code == 400
    assert "no node named" in r.json()["detail"].lower()


def test_setup_test_requires_a_fingerprint_for_https(fresh_app):
    r = fresh_app.post("/api/setup/test", json=_pve_conn() | {"scheme": "https"})
    assert r.status_code == 400
    assert "fingerprint" in r.json()["detail"].lower()


def test_setup_test_accepts_a_good_connection(fresh_app):
    r = fresh_app.post("/api/setup/test", json=_pve_conn())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["node"] == "hella"
    assert body["guests"] > 0


def test_setup_test_returns_the_guests_for_the_vm_picker(fresh_app):
    """The wizard offers a picker for the first user's VM, so the probe must say
    what actually exists rather than making someone recall a VMID."""
    body = fresh_app.post("/api/setup/test", json=_pve_conn()).json()
    guests = body["guest_list"]
    assert len(guests) == body["guests"]
    assert all("vmid" in g and "name" in g for g in guests)
    assert guests == sorted(guests, key=lambda g: g["vmid"])
    assert any(g["name"] for g in guests)  # names, not just ids


def test_setup_persists_nothing_when_the_connection_fails(fresh_app):
    r = fresh_app.post(
        "/api/setup",
        json={
            "pve": _pve_conn() | {"port": 9},
            "admin": {"username": "admin", "password": "adminpass1"},
        },
    )
    assert r.status_code == 400
    # No admin was created — setup is still needed.
    assert fresh_app.get("/api/setup/status").json()["needed"] is True
    assert fresh_app.post(
        "/api/login", json={"username": "admin", "password": "adminpass1"}
    ).status_code == 401


# --- the happy path ----------------------------------------------------------


def test_setup_commits_and_signs_the_admin_in(fresh_app):
    r = fresh_app.post(
        "/api/setup",
        json={
            "pve": _pve_conn(),
            "admin": {"username": "operator", "password": "operatorpw1"},
            "user": {"username": "customer", "password": "customerpw1", "vmid": 105},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["user"] == "operator" and body["role"] == "admin"
    assert body["node"] == "hella"
    assert body["csrf"]

    # The response signed us in — the session works without a separate login.
    me = fresh_app.get("/api/session")
    assert me.status_code == 200
    assert me.json()["user"] == "operator"

    # The panel is now live against Proxmox (no restart needed).
    vms = fresh_app.get("/api/vms")
    assert vms.status_code == 200
    assert len(vms.json()) > 0

    # Setup has closed behind us.
    assert fresh_app.get("/api/setup/status").json()["needed"] is False
    assert fresh_app.post(
        "/api/setup",
        json={"pve": _pve_conn(), "admin": {"username": "second", "password": "secondpw1"}},
    ).status_code == 409

    # Both accounts exist and work.
    assert fresh_app.post(
        "/api/login", json={"username": "customer", "password": "customerpw1"}
    ).status_code == 200


def test_setup_without_a_first_user_is_fine(fresh_app):
    r = fresh_app.post(
        "/api/setup",
        json={
            "pve": _pve_conn(),
            "admin": {"username": "solo", "password": "solopass1"},
            "user": None,
        },
    )
    assert r.status_code == 200, r.text
    fresh_app.cookies.clear()
    assert fresh_app.post(
        "/api/login", json={"username": "solo", "password": "solopass1"}
    ).status_code == 200


def test_setup_rejects_a_short_password(fresh_app):
    r = fresh_app.post(
        "/api/setup",
        json={"pve": _pve_conn(), "admin": {"username": "admin", "password": "short"}},
    )
    assert r.status_code == 422


def test_setup_rejects_a_duplicate_username(fresh_app):
    r = fresh_app.post(
        "/api/setup",
        json={
            "pve": _pve_conn(),
            "admin": {"username": "same", "password": "samepass11"},
            "user": {"username": "same", "password": "samepass22", "vmid": None},
        },
    )
    assert r.status_code == 400


# --- env always wins over stored config --------------------------------------


def test_env_config_is_not_overridden_by_stored_config():
    """An operator keeping secrets in agenix/systemd must never be overridden."""
    from hlidskjalf.config import Settings, apply_stored

    # HLIDSKJALF_PVE_HOST is set by the test env; pve_token_id is not.
    s = Settings()
    assert "pve_host" in s.model_fields_set
    assert "pve_token_id" not in s.model_fields_set

    apply_stored(s, {"pve_host": "db-host", "pve_token_id": "db-token-id"})
    assert s.pve_host == os.environ["HLIDSKJALF_PVE_HOST"]  # env wins
    assert s.pve_token_id == "db-token-id"  # not in env → stored value applies


def test_apply_stored_ignores_keys_outside_the_allowlist():
    """The setup endpoint is unauthenticated — it must not reach arbitrary settings."""
    from hlidskjalf.config import Settings, apply_stored

    s = Settings()
    apply_stored(
        s,
        {"admin_password_hash": "pwned", "debug": "true", "pve_token_id": "allowed"},
    )
    assert s.admin_password_hash != "pwned"  # not writable by setup
    assert s.debug is False  # not writable by setup
    assert s.pve_token_id == "allowed"  # is on the allowlist
