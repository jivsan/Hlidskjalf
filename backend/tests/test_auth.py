"""Login, session, CSRF, and login rate limiting."""

from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

from hlidskjalf.auth import COOKIE_NAME


def test_login_bad_credentials(anon):
    r = anon.post("/api/login", json={"username": ADMIN_USER, "password": "wrong"})
    assert r.status_code == 401
    r = anon.post("/api/login", json={"username": "nobody", "password": ADMIN_PASSWORD})
    assert r.status_code == 401


def test_login_good_credentials_sets_cookie_and_csrf(anon):
    r = anon.post(
        "/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["csrf"]
    assert COOKIE_NAME in r.cookies


def test_session_roundtrip(auth_client):
    r = auth_client.get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == ADMIN_USER
    # /api/session must hand back the same CSRF token the login issued
    assert body["csrf"] == auth_client.csrf


def test_session_requires_cookie(anon):
    assert anon.get("/api/session").status_code == 401
    assert anon.get("/api/vms").status_code == 401


def test_mutation_without_csrf_header_forbidden(auth_client):
    # vmid 140 is not protected; the 403 here must come from the CSRF check
    r = auth_client.post("/api/vms/140/status/start")
    assert r.status_code == 403
    assert "X-Hlidskjalf-CSRF" in r.json()["detail"]


def test_mutation_with_csrf_header_passes(auth_client):
    r = auth_client.post(
        "/api/vms/140/status/start", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 200
    assert r.json()["upid"].startswith("UPID:")


def test_mutation_with_wrong_csrf_header_forbidden(auth_client):
    r = auth_client.post(
        "/api/vms/140/status/start", headers={"X-Hlidskjalf-CSRF": "bogus"}
    )
    assert r.status_code == 403


def test_login_rate_limit(anon):
    for _ in range(5):
        r = anon.post("/api/login", json={"username": ADMIN_USER, "password": "wrong"})
        assert r.status_code == 401
    r = anon.post("/api/login", json={"username": ADMIN_USER, "password": "wrong"})
    assert r.status_code == 429
    # even correct credentials are refused while rate-limited
    r = anon.post(
        "/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 429
