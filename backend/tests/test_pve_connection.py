"""Settings → Proxmox: editing the connection after setup has closed.

Why this exists: the Proxmox connection used to be settable ONLY in the first-run
wizard, and the wizard closes forever once a user exists. So rotating a token,
re-pinning a renewed certificate, or moving the host meant editing sqlite by hand
on the server. That is a bad place to leave an operator — especially with an ACME
certificate, whose fingerprint changes on every renewal (~60 days) and takes a
pinned panel offline with it.

The fix is an authenticated editor, NOT a "reset to wizard" button: the setup
endpoints are unauthenticated by construction, and reopening them would hand
anyone who can reach the panel a window to make themselves an admin. That rule is
asserted here so nobody quietly adds one later.
"""

import ssl

import pytest
from conftest import csrf_headers

from hlidskjalf.config import Settings, get_settings
from hlidskjalf.probe import PveConn, check_tls_choice
from hlidskjalf.pve import PveClient, make_system_ssl_context

TENANT_USER, TENANT_PASS = "tenant-pve", "tenant-password"

CONNECTION_ENV = (
    "HLIDSKJALF_PVE_HOST", "HLIDSKJALF_PVE_PORT", "HLIDSKJALF_PVE_NODE",
    "HLIDSKJALF_PVE_SCHEME", "HLIDSKJALF_PVE_TOKEN_ID",
    "HLIDSKJALF_PVE_TOKEN_SECRET", "HLIDSKJALF_PVE_FINGERPRINT", "HLIDSKJALF_PVE_TLS",
)


@pytest.fixture()
def wizard_configured(monkeypatch):
    """Model a panel configured through the WIZARD, not the environment.

    The test harness is env-configured, and the panel refuses to edit a connection
    the environment owns (env always wins — a change would silently revert on the
    next restart). Two things encode that ownership and both must be lifted here:
    the environment itself, and `model_fields_set`, which is what config.apply_stored
    consults.
    """
    for var in CONNECTION_ENV:
        monkeypatch.delenv(var, raising=False)
    s = get_settings()
    was = set(s.model_fields_set)
    s.model_fields_set.difference_update(
        {v.removeprefix("HLIDSKJALF_").lower() for v in CONNECTION_ENV}
    )
    yield s
    s.model_fields_set.clear()
    s.model_fields_set.update(was)


def _tenant(auth_client):
    """A regular (non-admin) session."""
    r = auth_client.post(
        "/api/users",
        json={"username": TENANT_USER, "password": TENANT_PASS, "role": "user", "vmid": 140},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code in (201, 409), r.text
    auth_client.cookies.clear()
    r = auth_client.post("/api/login", json={"username": TENANT_USER, "password": TENANT_PASS})
    assert r.status_code == 200
    auth_client.csrf = r.json()["csrf"]
    return auth_client


def _conn(client, **overrides):
    """The connection the test harness itself is using (the mock PVE), which is
    therefore known-good — so a rejection means the panel rejected it, not the
    network."""
    body = {
        "host": "127.0.0.1",
        "port": int(client.app.state.pve.settings.pve_port),
        "node": "pve",
        "scheme": "http",
        "token_id": "hlidskjalf@pve!panel",
        "token_secret": "mock-secret",
        "fingerprint": "",
        "tls": "pin",
    }
    body.update(overrides)
    return body


# --- the setup endpoints must stay shut -------------------------------------


def test_setup_endpoints_stay_closed_once_a_user_exists(auth_client):
    """The house rule, asserted. If someone adds a 'reset to wizard' button, this
    test is what should stop them: an unauthenticated endpoint that can configure
    the panel is a takeover window on the LAN."""
    for path, body in (
        ("/api/setup/test", _conn(auth_client)),
        ("/api/setup", {"pve": _conn(auth_client),
                        "admin": {"username": "someone", "password": "hunter2hunter2"}}),
    ):
        r = auth_client.post(path, json=body)
        assert r.status_code == 409, f"{path} answered {r.status_code} — setup must be closed"


# --- reading the connection --------------------------------------------------


def test_get_connection_never_returns_the_token_secret(auth_client):
    r = auth_client.get("/api/settings/pve")
    assert r.status_code == 200
    body = r.json()
    assert "token_secret" not in body
    assert body["token_secret_set"] is True     # only whether one exists
    assert body["node"] == "pve"
    assert body["tls"] in ("pin", "system")
    assert "mock-secret" not in r.text


def test_connection_is_admin_only(auth_client):
    c = _tenant(auth_client)
    assert c.get("/api/settings/pve").status_code == 403
    r = c.put("/api/settings/pve", json=_conn(c), headers=csrf_headers(c))
    assert r.status_code == 403


def test_connection_change_requires_csrf(auth_client):
    r = auth_client.put("/api/settings/pve", json=_conn(auth_client))  # no CSRF header
    assert r.status_code == 403


# --- writing it --------------------------------------------------------------


def test_a_connection_that_does_not_answer_is_never_persisted(auth_client, wizard_configured):
    """The whole point of the live test: a saved-but-broken connection leaves the
    panel unable to reach Proxmox, with no way back except the database."""
    before = auth_client.get("/api/settings/pve").json()
    r = auth_client.put(
        "/api/settings/pve",
        json=_conn(auth_client, port=9),  # discard port: nothing listens
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "Could not reach Proxmox" in r.json()["detail"]
    assert auth_client.get("/api/settings/pve").json() == before  # unchanged


def test_wrong_node_name_is_refused_with_what_the_host_actually_has(auth_client, wizard_configured):
    r = auth_client.put(
        "/api/settings/pve",
        json=_conn(auth_client, node="not-a-node"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "no node named 'not-a-node'" in detail and "pve" in detail


def test_changing_the_connection_takes_effect_without_a_restart(auth_client, wizard_configured):
    """And the client is reconfigured IN PLACE — the metrics source and the
    bandwidth accumulator hold a reference to it, so a rebind would leave them
    talking to a closed client."""
    pve_before = auth_client.app.state.pve

    r = auth_client.put(
        "/api/settings/pve",
        json=_conn(auth_client, token_id="hlidskjalf@pve!rotated"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["token_id"] == "hlidskjalf@pve!rotated"

    assert auth_client.app.state.pve is pve_before          # same object
    assert pve_before.settings.pve_token_id == "hlidskjalf@pve!rotated"
    assert auth_client.get("/api/vms").status_code == 200   # still talks to PVE

    # put it back so later tests see the original token id
    auth_client.put("/api/settings/pve", json=_conn(auth_client),
                    headers=csrf_headers(auth_client))


def test_the_secret_can_be_left_blank_to_keep_the_stored_one(auth_client, wizard_configured):
    """The secret is never sent to the browser, so it must be possible to change
    the node or the fingerprint without retyping it."""
    r = auth_client.put(
        "/api/settings/pve",
        json=_conn(auth_client, token_secret=""),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 200, r.text
    assert auth_client.get("/api/vms").status_code == 200   # stored secret still works


def test_a_bad_tls_mode_is_refused(auth_client, wizard_configured):
    r = auth_client.put(
        "/api/settings/pve",
        json=_conn(auth_client, tls="whatever"),
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400


# --- the TLS choice itself ---------------------------------------------------


def test_https_with_no_fingerprint_and_no_ca_mode_is_refused():
    """There is no 'just trust it' mode, and there never will be."""
    with pytest.raises(Exception) as e:
        check_tls_choice(PveConn(host="pve.example.org", node="pve", scheme="https",
                                 token_id="t@pve!x", token_secret="s", fingerprint=""))
    assert "fingerprint" in str(e.value.detail)


def test_system_ca_mode_against_an_ip_is_refused():
    """A CA-issued certificate is issued to a NAME. Verifying one against an IP
    literal cannot succeed, so say so instead of failing at handshake time."""
    with pytest.raises(Exception) as e:
        check_tls_choice(PveConn(host="192.0.2.10", node="pve", scheme="https",
                                 token_id="t@pve!x", token_secret="s", tls="system"))
    assert "issued to a name" in str(e.value.detail)


def test_system_ca_mode_with_a_hostname_needs_no_fingerprint():
    check_tls_choice(PveConn(host="pve.example.org", node="pve", scheme="https",
                             token_id="t@pve!x", token_secret="s", tls="system"))


def test_system_context_verifies_chain_and_hostname():
    ctx = make_system_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_client_in_system_mode_does_not_pin(monkeypatch):
    """Regression guard: the pinned context sets CERT_NONE, and if system mode ever
    fell through to it, an ACME cert would be accepted without ANY verification."""
    s = Settings(
        pve_host="pve.example.org", pve_scheme="https", pve_tls="system",
        pve_token_id="t@pve!x", pve_token_secret="s", pve_fingerprint="",
    )
    client = PveClient(s)
    assert client.ssl_context is not None
    assert client.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert client.ssl_context.check_hostname is True


def test_client_in_pin_mode_still_refuses_https_without_a_pin():
    s = Settings(
        pve_host="10.0.0.1", pve_scheme="https", pve_tls="pin",
        pve_token_id="t@pve!x", pve_token_secret="s", pve_fingerprint="",
    )
    with pytest.raises(RuntimeError, match="FINGERPRINT is required"):
        PveClient(s)
