"""Security-hardening regression tests.

Covers the five fixes on the harden/security branch:
1. The global exception handler never leaks tracebacks/error types/messages to
   clients (even with settings.debug=True), while admins still get the full
   traceback via GET /api/debug/errors.
2. The session cookie honours the cookie_secure setting (Secure flag).
3. The switch eAPI TLS `verify` argument is selected correctly
   (pinned fingerprint / system CAs / disabled).
4. The legacy env-hash admin fallback is closed once real users exist.
5. The login rate limit is per client IP (one IP tripping does not lock others).
"""

import ssl

import pytest
from fastapi import HTTPException

from conftest import ADMIN_PASSWORD, ADMIN_USER


# --- Fix 1: exception handler never leaks internals -------------------------


def test_unhandled_exception_never_leaks_to_client(auth_client):
    """A 500 body is generic even with debug=True; admins still see the traceback."""
    from fastapi.testclient import TestClient

    from hlidskjalf.config import get_settings
    from hlidskjalf.main import app

    marker = "boom-secret-xyz-do-not-leak"

    async def _boom():
        raise RuntimeError(marker)

    app.add_api_route("/api/_test_boom", _boom, methods=["GET"])

    settings = get_settings()
    original_debug = settings.debug
    settings.debug = True  # worst case: even in debug mode the client sees nothing
    try:
        # A dedicated client that returns the 500 response instead of re-raising.
        raw = TestClient(app, raise_server_exceptions=False)
        r = raw.get("/api/_test_boom")

        assert r.status_code == 500
        body = r.json()
        assert body == {"detail": "Internal server error"}
        # No internals anywhere in the response body.
        assert marker not in r.text
        assert "traceback" not in body
        assert "error_type" not in body
        assert "RuntimeError" not in r.text

        # Admins still get the full detail via the admin-gated debug endpoint.
        errs = auth_client.get("/api/debug/errors")
        assert errs.status_code == 200
        entries = errs.json()
        match = next((e for e in entries if marker in (e.get("error") or "")), None)
        assert match is not None, "error was not recorded in the admin buffer"
        assert match.get("error_type") == "RuntimeError"
        assert "traceback" in match and marker in match["traceback"]
    finally:
        settings.debug = original_debug
        app.router.routes = [
            rt for rt in app.router.routes
            if getattr(rt, "path", None) != "/api/_test_boom"
        ]


def test_debug_errors_endpoint_requires_admin(anon):
    """The traceback buffer is admin-only; anonymous callers get 401."""
    assert anon.get("/api/debug/errors").status_code == 401


# --- Fix 2: session cookie Secure flag --------------------------------------


def test_cookie_secure_flag_honoured():
    from starlette.responses import Response

    from hlidskjalf import auth
    from hlidskjalf.config import get_settings

    settings = get_settings()
    original = settings.cookie_secure
    try:
        settings.cookie_secure = True
        r = Response()
        auth.start_session(r, ADMIN_USER)
        set_cookie = r.headers.get("set-cookie")
        assert set_cookie is not None
        assert "Secure" in set_cookie
        # unrelated hardening flags must remain set
        assert "HttpOnly" in set_cookie
        assert "samesite=strict" in set_cookie.lower()

        settings.cookie_secure = False
        r2 = Response()
        auth.start_session(r2, ADMIN_USER)
        set_cookie2 = r2.headers.get("set-cookie")
        assert "Secure" not in set_cookie2
    finally:
        settings.cookie_secure = original


# --- Fix 3: switch eAPI TLS verification selection --------------------------


def _switch_settings(**overrides):
    from hlidskjalf.config import Settings

    return Settings(**overrides)


def test_switch_verify_default_uses_system_cas():
    from hlidskjalf.switch import select_switch_verify

    s = _switch_settings(switch_fingerprint="", switch_verify=True)
    assert select_switch_verify(s) is True


def test_switch_verify_disabled_returns_false_and_warns(caplog):
    import hlidskjalf.switch as switch_mod
    from hlidskjalf.switch import select_switch_verify

    switch_mod._warned_switch_unverified = False  # allow the warning to fire
    s = _switch_settings(switch_fingerprint="", switch_verify=False)
    with caplog.at_level("WARNING", logger="hlidskjalf.switch"):
        result = select_switch_verify(s)
    assert result is False
    assert any("verification is DISABLED" in rec.message for rec in caplog.records)


def test_switch_fingerprint_returns_pinned_context():
    from hlidskjalf.switch import select_switch_verify

    # fingerprint wins even if switch_verify would otherwise be True
    s = _switch_settings(switch_fingerprint="AA:BB:CC:DD", switch_verify=True)
    ctx = select_switch_verify(s)
    assert isinstance(ctx, ssl.SSLContext)


def test_arista_client_picks_verify_from_settings():
    from hlidskjalf.switch import AristaClient

    # Default test settings: no fingerprint, verify on -> True (never verify=False).
    c = AristaClient()
    assert c._verify is True


# --- Fix 4: legacy admin backdoor closed once users exist -------------------


def test_legacy_admin_disabled_when_users_exist(anon, monkeypatch):
    """With users seeded, even a "valid" legacy env-hash must not grant a login."""
    from hlidskjalf import auth

    # Pretend the legacy env-hash check passes for anything.
    monkeypatch.setattr(auth, "_legacy_verify", lambda u, p: True)
    r = anon.post("/api/login", json={"username": "backdoor", "password": "whatever"})
    # The session DB has the bootstrapped admin, so the legacy path is closed.
    assert r.status_code == 401


# --- Fix 5: per-IP login rate limit -----------------------------------------


def test_login_rate_limit_is_per_ip():
    from hlidskjalf import auth

    auth.reset_login_rate()
    ip_a = "203.0.113.10"
    ip_b = "198.51.100.20"

    # First LOGIN_RATE attempts from ip_a are allowed.
    for _ in range(auth.LOGIN_RATE):
        auth.check_login_rate(ip_a)

    # The next attempt from ip_a is refused.
    with pytest.raises(HTTPException) as ei:
        auth.check_login_rate(ip_a)
    assert ei.value.status_code == 429

    # A different IP is unaffected by ip_a hitting the limit.
    auth.check_login_rate(ip_b)  # must not raise
    auth.reset_login_rate()


def test_login_rate_limit_over_http_still_trips(anon):
    """The HTTP login endpoint still 429s after LOGIN_RATE bad attempts."""
    from hlidskjalf import auth

    for _ in range(auth.LOGIN_RATE):
        r = anon.post("/api/login", json={"username": ADMIN_USER, "password": "wrong"})
        assert r.status_code == 401
    r = anon.post("/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 429
