"""The panel is going on the internet. Admin is not going with it.

The model: tenants reach the panel from anywhere (that is the point — they manage
their own VM), while **admin exists only inside `admin_networks`**. The exposure is
a Cloudflare tunnel; the admin door is the LAN/tailnet.

Three things have to be true, and each is a separate way to get this wrong:

1. **The panel must know who is calling.** Behind a proxy every request arrives from
   the proxy. Trust the socket peer and the audit log says "127.0.0.1" for everyone
   and the per-IP rate limiter becomes one global bucket. Trust the *headers*
   unconditionally and anyone on the internet can claim to be on your LAN.
2. **An admin cannot log in from outside.** Not "cannot see the admin pages".
3. **An admin session that arrives from outside does not work.** This is the one that
   bites: a session cookie travels with the browser. Sign in at home, open the same
   laptop on café wifi — the cookie is still valid, and ~20 routes branch on
   `is_admin(user)` directly. The refusal has to live at the session layer.
"""

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

from pydantic import ValidationError

from hlidskjalf.config import Settings, get_settings
from hlidskjalf.netzone import client_ip, in_networks

TAILNET = "100.64.0.0/10"
INSIDE = "100.101.102.103"     # a tailnet address
OUTSIDE = "203.0.113.7"        # somewhere on the internet
PROXY = "127.0.0.1"            # traefik / cloudflared, same host


@pytest.fixture()
def exposed(monkeypatch, client):
    """A panel that is reachable publicly: proxied, with admin pinned to the tailnet.

    The TestClient normally presents a peer of ("testclient", 50000), which is not an
    address at all. A deployment behind Traefik/cloudflared sees a real socket peer of
    127.0.0.1 and the caller's address in a forwarded header, so model exactly that —
    otherwise these tests would prove nothing about the code that actually runs.
    """
    s = get_settings()
    monkeypatch.setattr(s, "trusted_proxies", [f"{PROXY}/32"])
    monkeypatch.setattr(s, "admin_networks", [TAILNET])
    monkeypatch.setattr(client._transport, "client", (PROXY, 50000))
    yield s


def _as(client, ip: str) -> dict:
    """Headers of a request arriving through the trusted proxy, from `ip`."""
    return {"X-Forwarded-For": ip}


# --- 1. who is calling -------------------------------------------------------


class _Req:
    """Minimal stand-in for a Request: peer address + headers."""

    def __init__(self, peer: str, headers: dict | None = None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}


def test_forwarded_headers_are_ignored_from_an_untrusted_peer():
    """The whole game. If a direct caller could set X-Forwarded-For, anyone on the
    internet would simply claim a tailnet address and be an admin."""
    req = _Req(OUTSIDE, {"x-forwarded-for": INSIDE, "cf-connecting-ip": INSIDE})
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE
    assert not in_networks(client_ip(req, [f"{PROXY}/32"]), [TAILNET])


def test_forwarded_headers_are_believed_from_the_trusted_proxy():
    req = _Req(PROXY, {"x-forwarded-for": OUTSIDE})
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE


def test_cloudflare_header_wins_only_in_cloudflare_mode():
    req = _Req(PROXY, {"cf-connecting-ip": OUTSIDE, "x-forwarded-for": f"{INSIDE}, {PROXY}"})
    assert client_ip(req, [f"{PROXY}/32"], trust_cf=True) == OUTSIDE


def test_cf_connecting_ip_is_ignored_when_not_behind_cloudflare():
    """A non-Cloudflare proxy forwards a client-set CF-Connecting-IP verbatim. Off
    cloudflare mode (the default) it must be ignored and the X-Forwarded-For chain
    used instead — otherwise anyone could name a tailnet address and be an admin."""
    req = _Req(PROXY, {"cf-connecting-ip": INSIDE, "x-forwarded-for": OUTSIDE})
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE            # default: cf ignored
    assert client_ip(req, [f"{PROXY}/32"], trust_cf=False) == OUTSIDE


def test_a_prepended_forwarded_address_cannot_forge_the_client():
    """XFF is client-controlled at the left. A hostile client sends
    "100.64.0.1" and the real proxy appends the true address — so we walk from the
    RIGHT, past our own proxies, and take the first address we did not add."""
    req = _Req(PROXY, {"x-forwarded-for": f"{INSIDE}, {OUTSIDE}"})
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE


def test_with_no_trusted_proxy_the_socket_peer_is_the_truth():
    req = _Req(OUTSIDE, {"x-forwarded-for": INSIDE})
    assert client_ip(req, []) == OUTSIDE


# --- 2. admin cannot log in from outside -------------------------------------


def test_admin_login_is_refused_from_the_public_internet(anon, exposed):
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, OUTSIDE),
    )
    assert r.status_code == 403
    assert "local network" in r.json()["detail"]
    assert not r.cookies  # and no session was issued


def test_spoofed_cf_connecting_ip_does_not_grant_admin(anon, exposed):
    """The netzone finding, end to end: not behind Cloudflare (the default), a caller
    must not forge admin-zone membership with CF-Connecting-IP. A public request that
    names a tailnet address in that header is still an outside request and is refused."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers={"X-Forwarded-For": OUTSIDE, "CF-Connecting-IP": INSIDE},
    )
    assert r.status_code == 403, "spoofed CF-Connecting-IP was believed; admin login allowed from outside"
    assert not r.cookies


def test_admin_login_works_from_the_tailnet(anon, exposed):
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"


def test_a_tenant_can_log_in_from_anywhere(auth_client, anon, exposed):
    """The entire reason the panel is exposed: a friend, on the internet, reaching
    their own VM."""
    r = auth_client.post(
        "/api/users",
        json={"username": "friend", "password": "friend-password", "role": "user", "vmid": 151},
        headers={**csrf_headers(auth_client), **_as(auth_client, INSIDE)},
    )
    assert r.status_code in (201, 409), r.text   # 409 once the session db has it
    anon.cookies.clear()
    r = anon.post(
        "/api/login",
        json={"username": "friend", "password": "friend-password"},
        headers=_as(anon, OUTSIDE),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "user"
    assert r.json()["vmid"] == 151


# --- 3. an admin session from outside does not work --------------------------


def test_an_admin_session_stops_working_when_it_leaves_the_admin_network(anon, exposed):
    """The cookie travels with the browser. Signing in at home and then opening the
    laptop somewhere else must NOT carry admin with it."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200, r.text
    anon.csrf = r.json()["csrf"]
    auth_client = anon
    assert auth_client.get("/api/vms", headers=_as(auth_client, INSIDE)).status_code == 200

    r = auth_client.get("/api/vms", headers=_as(auth_client, OUTSIDE))
    assert r.status_code == 403, "the admin session was honoured from the public internet"
    assert "Administration is restricted" in r.json()["detail"]


def test_admin_routes_are_refused_from_outside(auth_client, exposed):
    for path in ("/api/users", "/api/settings/pve", "/api/version", "/api/templates"):
        r = auth_client.get(path, headers=_as(auth_client, OUTSIDE))
        assert r.status_code == 403, f"{path} answered {r.status_code} from the internet"


def test_mutations_are_refused_from_outside(anon, exposed):
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200
    anon.csrf = r.json()["csrf"]
    auth_client = anon
    r = auth_client.request(
        "DELETE",
        "/api/vms/140",
        json={"confirm_name": "scratch-old"},
        headers={**csrf_headers(auth_client), **_as(auth_client, OUTSIDE)},
    )
    assert r.status_code == 403


def test_nothing_changes_for_a_panel_that_is_not_exposed(auth_client):
    """admin_networks empty (the default) = a LAN-only panel = admin from anywhere.
    Every existing deployment must keep working exactly as before."""
    assert not get_settings().admin_networks
    assert auth_client.get("/api/vms", headers=_as(auth_client, OUTSIDE)).status_code == 200
    assert auth_client.get("/api/users", headers=_as(auth_client, OUTSIDE)).status_code == 200


# --- 4. an internet-facing login page ---------------------------------------


def test_an_account_backs_off_after_repeated_failures_from_different_addresses(anon, exposed):
    """Per-IP limiting is no defence against a botnet: every guess arrives from a
    fresh address, so every bucket stays empty. The ACCOUNT has to back off too."""
    from hlidskjalf import auth

    auth.reset_login_rate()
    for i in range(auth.ACCOUNT_FAILURES):
        r = anon.post(
            "/api/login",
            json={"username": ADMIN_USER, "password": "wrong"},
            headers=_as(anon, f"203.0.113.{i + 10}"),   # a different source each time
        )
        assert r.status_code in (401, 403)

    # Now even the RIGHT password, from a fresh address, is refused for a while.
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, "203.0.113.200"),
    )
    assert r.status_code == 429
    assert "failed sign-ins" in r.json()["detail"]


def test_a_successful_sign_in_clears_the_backoff(anon, exposed):
    from hlidskjalf import auth

    auth.reset_login_rate()
    for i in range(auth.ACCOUNT_FAILURES - 1):
        anon.post(
            "/api/login",
            json={"username": ADMIN_USER, "password": "wrong"},
            headers=_as(anon, f"203.0.113.{i + 10}"),
        )
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200, r.text
    assert not auth._failed_by_user.get(ADMIN_USER)


# --- 5. the interlock: `public` refuses an unsafe config ---------------------
#
# netzone.py only defends a panel that was actually TOLD its boundaries. The
# footgun is exposing the panel while admin_networks is empty (admin from
# anywhere) or trusted_proxies is empty (blind to the real caller). `public`
# makes that combination refuse to load at all — and because these three fields
# are env-only (never wizard/DB-writable), the check is final at construction.


def _settings(**kw) -> Settings:
    """Settings from explicit values, immune to the ambient test environment."""
    base = dict(
        public=True,
        admin_networks=["100.64.0.0/10"],
        trusted_proxies=["127.0.0.1/32"],
    )
    base.update(kw)
    return Settings(**base)


def test_public_without_admin_networks_refuses_to_load():
    with pytest.raises(ValidationError) as e:
        _settings(admin_networks=[])
    msg = str(e.value)
    assert "admin_networks" in msg and "HLIDSKJALF_PUBLIC" in msg


def test_public_without_trusted_proxies_refuses_to_load():
    with pytest.raises(ValidationError) as e:
        _settings(trusted_proxies=[])
    assert "trusted_proxies" in str(e.value)


def test_public_without_either_boundary_names_both():
    with pytest.raises(ValidationError) as e:
        _settings(admin_networks=[], trusted_proxies=[])
    msg = str(e.value)
    assert "admin_networks" in msg and "trusted_proxies" in msg


def test_public_with_both_boundaries_loads():
    s = _settings()
    assert s.public and s.admin_networks and s.trusted_proxies


def test_a_lan_only_panel_is_never_constrained():
    """public=False (the default) may leave both empty — the LAN-only posture is
    unchanged, and a fresh clone must keep booting."""
    s = Settings(public=False, admin_networks=[], trusted_proxies=[])
    assert not s.public
