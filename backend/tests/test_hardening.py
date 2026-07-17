"""Hardening added after the v0.3.6 self-audit.

Each test here corresponds to a gap that was real:
- logout deleted the cookie but revoked nothing
- only login was rate limited; destroy/provision/power were wide open
- the CSRF token was HMAC(username) — a permanent constant that never rotated
- there was no durable record of who destroyed what
- /api/debug/config redacted by guessing at key names
"""

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

VMID = 115


def login(client, username=ADMIN_USER, password=ADMIN_PASSWORD):
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.csrf = r.json()["csrf"]
    return r


@pytest.fixture
def user_factory(auth_client):
    """Create users as the admin; clean them up afterwards."""
    created: list[str] = []

    def create(username: str, password: str = "secretpw1", role: str = "user", vmid=None):
        body = {"username": username, "password": password, "role": role}
        if vmid is not None:
            body["vmid"] = vmid
        r = auth_client.post("/api/users", json=body, headers=csrf_headers(auth_client))
        if r.status_code == 201:
            created.append(username)
        return r

    yield create

    login(auth_client)
    for name in created:
        auth_client.delete(f"/api/users/{name}", headers=csrf_headers(auth_client))


# --- logout actually revokes -------------------------------------------------


def test_logout_kills_a_copy_of_the_cookie(client):
    """Deleting the cookie only asks the browser nicely. A cookie someone else
    copied has to stop working too."""
    login(client)
    stolen = dict(client.cookies)
    assert client.get("/api/session").status_code == 200

    client.post("/api/logout", headers=csrf_headers(client))

    # Replay the captured cookie, exactly as a thief would.
    client.cookies.clear()
    for k, v in stolen.items():
        client.cookies.set(k, v)
    assert client.get("/api/session").status_code == 401


def test_logging_out_one_session_leaves_the_others_alone(client):
    """Revocation is per-session, not per-user: signing out on your laptop must
    not sign you out on your phone."""
    login(client)
    first = dict(client.cookies)

    login(client)  # a second, independent session
    second = dict(client.cookies)
    assert first != second

    client.post("/api/logout", headers=csrf_headers(client))  # kills `second`

    client.cookies.clear()
    for k, v in first.items():
        client.cookies.set(k, v)
    assert client.get("/api/session").status_code == 200  # still alive


# --- CSRF rotates with the password ------------------------------------------


def test_csrf_token_changes_when_the_password_changes(client, user_factory):
    """It used to be HMAC(username) — the same forever. Leak it once (a log, a
    screenshot) and it stayed valid for the life of the account."""
    assert user_factory("csrf-rot", password="origpass1").status_code == 201

    login(client, "csrf-rot", "origpass1")
    before = client.csrf

    r = client.post(
        "/api/users/csrf-rot/password",
        json={"password": "rotated-pw1", "current_password": "origpass1"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 200

    login(client, "csrf-rot", "rotated-pw1")
    assert client.csrf != before, "CSRF token did not rotate with the password"


# --- rate limits on the dangerous verbs --------------------------------------


def test_power_actions_are_rate_limited(client):
    login(client)
    codes = [
        client.post(f"/api/vms/{VMID}/status/start", headers=csrf_headers(client)).status_code
        for _ in range(35)  # limit is 30/min
    ]
    assert 429 in codes, "power actions are unthrottled — a stolen session can hammer PVE"


def test_destroy_is_rate_limited_hard(client):
    """5/hour. Destroy is the one action you cannot take back."""
    login(client)
    codes = []
    for _ in range(7):
        r = client.request(
            "DELETE",
            "/api/vms/999999",  # does not exist; we only care about the limiter
            json={"confirm_name": "nope"},
            headers=csrf_headers(client),
        )
        codes.append(r.status_code)
    assert 429 in codes


def test_console_ticket_mint_is_rate_limited(client):
    """The console mint was the one PVE-hitting verb the v0.3.6 pass missed.

    Every GET /api/vms/{vmid}/console is a vncproxy/termproxy POST against
    Proxmox, so a tenant looping it is looping the hypervisor. 30/hour per
    user: an interactive console mints a ticket per page load and per
    reconnect — a handful an hour — so only something scripted ever hits this.

    (No audit-log assertion here on purpose: rate-limit refusals are not
    journaled for ANY verb — check_rate raises inside the dependency, before
    the handler's journal.record can run — so console matches the rest.)
    """
    login(client)
    codes = [client.get(f"/api/vms/{VMID}/console").status_code for _ in range(31)]
    assert codes[:30] == [200] * 30, "ordinary console use must not be throttled"
    assert codes[30] == 429, "the 31st ticket within the hour must be refused"


def test_the_limit_is_per_user_not_global(client, user_factory):
    """One tenant burning their quota must not lock everybody else out."""
    assert user_factory("noisy", password="noisypass1", vmid=VMID).status_code == 201

    login(client, "noisy", "noisypass1")
    for _ in range(35):
        client.post(f"/api/vms/{VMID}/status/start", headers=csrf_headers(client))

    login(client)  # admin, a different bucket
    r = client.post(f"/api/vms/{VMID}/status/start", headers=csrf_headers(client))
    assert r.status_code != 429


# --- the audit trail ---------------------------------------------------------


def _audit(client, **params):
    r = client.get("/api/debug/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_logins_and_failures_are_recorded(client, anon):
    anon.post("/api/login", json={"username": ADMIN_USER, "password": "wrong-password"})
    login(client)
    actions = {row["action"] for row in _audit(client, limit=50)}
    assert "auth.login" in actions
    assert "auth.login_failed" in actions


def test_power_actions_are_recorded_with_actor_and_target(client):
    login(client)
    client.post(f"/api/vms/{VMID}/status/start", headers=csrf_headers(client))
    rows = _audit(client, action="vm.power", limit=10)
    assert rows, "a power action left no trace"
    row = rows[0]
    assert row["actor"] == ADMIN_USER
    assert row["target"] == str(VMID)
    assert row["client"]  # we know where it came from
    assert row["ts"]


def test_a_refused_action_is_recorded_too(client, user_factory):
    """A denied destroy is exactly what you want to find later."""
    assert user_factory("prober", password="proberpw1", vmid=VMID).status_code == 201
    login(client, "prober", "proberpw1")

    r = client.request(
        "DELETE", "/api/vms/101", json={"confirm_name": "panel-host"}, headers=csrf_headers(client)
    )
    assert r.status_code == 403

    login(client)  # admin, to read the log
    rows = _audit(client, action="vm.destroy", limit=20)
    refused = [x for x in rows if x["actor"] == "prober"]
    assert refused, "a refused destroy left no trace"
    assert refused[0]["ok"] == 0
    assert refused[0]["target"] == "101"


def test_the_audit_log_is_admin_only(client, user_factory):
    assert user_factory("nosy", password="nosypass1", vmid=VMID).status_code == 201
    login(client, "nosy", "nosypass1")
    assert client.get("/api/debug/audit").status_code == 403


def test_the_audit_log_survives_a_restart(client):
    """The old debug buffers were in-memory and died with the process — which is
    precisely when you most want the record."""
    login(client)
    client.post(f"/api/vms/{VMID}/status/start", headers=csrf_headers(client))

    # Read it straight off disk, not out of the running app's memory.
    import sqlite3

    from hlidskjalf.config import get_settings

    conn = sqlite3.connect(get_settings().db_path)
    rows = conn.execute("SELECT actor, action FROM audit WHERE action='vm.power'").fetchall()
    conn.close()
    assert rows, "the audit trail is not actually persisted"


# --- redaction ---------------------------------------------------------------


def test_debug_config_redacts_every_declared_secret(auth_client):
    """Redaction is driven by the declared secret sets, not by guessing from the
    key name — a keyword denylist leaks the first secret whose name doesn't match."""
    from hlidskjalf import secretbox

    cfg = auth_client.get("/api/debug/config").json()
    for field in secretbox.SECRET_KEYS:
        if field in cfg:
            assert cfg[field] == "***REDACTED***", f"{field} leaked to the debug endpoint"


def test_debug_config_flags_that_nothing_is_protected(auth_client):
    """protected_vmids defaults to empty, which means an admin can destroy the VM
    that is running the panel. Say so out loud."""
    cfg = auth_client.get("/api/debug/config").json()
    assert "protected_vmids_empty" in cfg
