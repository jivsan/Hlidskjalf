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
from hlidskjalf.netzone import client_ip, in_networks, is_admin_zone

TAILNET = "100.64.0.0/10"
INSIDE = "100.101.102.103"     # a tailnet address
OUTSIDE = "203.0.113.7"        # somewhere on the internet
PROXY = "127.0.0.1"            # traefik / cloudflared, same host
PROXY2 = "127.0.0.2"           # a second trusted hop (tunnel -> local reverse proxy)


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
    """Minimal stand-in for a Request: peer address + headers.

    The headers MUST be Starlette's, not a dict: client_ip() reads EVERY line of
    a duplicated header via getlist(), and a plain dict cannot even hold two
    lines with the same name — a dict-based mock could never express the
    multi-line spoof this module now has to stop.
    """

    def __init__(self, peer: str, headers: list[tuple[str, str]] | None = None):
        from starlette.datastructures import Headers

        self.client = type("C", (), {"host": peer})()
        self.headers = Headers(
            raw=[(k.lower().encode(), v.encode()) for k, v in (headers or [])]
        )


def test_forwarded_headers_are_ignored_from_an_untrusted_peer():
    """The whole game. If a direct caller could set X-Forwarded-For, anyone on the
    internet would simply claim a tailnet address and be an admin."""
    req = _Req(OUTSIDE, [("x-forwarded-for", INSIDE), ("cf-connecting-ip", INSIDE)])
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE
    assert not in_networks(client_ip(req, [f"{PROXY}/32"]), [TAILNET])


def test_forwarded_headers_are_believed_from_the_trusted_proxy():
    req = _Req(PROXY, [("x-forwarded-for", OUTSIDE)])
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE


def test_cloudflare_header_wins_only_in_cloudflare_mode():
    req = _Req(PROXY, [("cf-connecting-ip", OUTSIDE), ("x-forwarded-for", f"{INSIDE}, {PROXY}")])
    assert client_ip(req, [f"{PROXY}/32"], trust_cf=True) == OUTSIDE


def test_cf_connecting_ip_is_ignored_when_not_behind_cloudflare():
    """A non-Cloudflare proxy forwards a client-set CF-Connecting-IP verbatim. Off
    cloudflare mode (the default) it must be ignored and the X-Forwarded-For chain
    used instead — otherwise anyone could name a tailnet address and be an admin."""
    req = _Req(PROXY, [("cf-connecting-ip", INSIDE), ("x-forwarded-for", OUTSIDE)])
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE            # default: cf ignored
    assert client_ip(req, [f"{PROXY}/32"], trust_cf=False) == OUTSIDE


def test_a_prepended_forwarded_address_cannot_forge_the_client():
    """XFF is client-controlled at the left. A hostile client sends
    "100.64.0.1" and the real proxy appends the true address — so we walk from the
    RIGHT, past our own proxies, and take the first address we did not add."""
    req = _Req(PROXY, [("x-forwarded-for", f"{INSIDE}, {OUTSIDE}")])
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE


def test_with_no_trusted_proxy_the_socket_peer_is_the_truth():
    req = _Req(OUTSIDE, [("x-forwarded-for", INSIDE)])
    assert client_ip(req, []) == OUTSIDE


# --- 1b. duplicate XFF LINES (the finding) ------------------------------------
#
# HTTP allows a list-valued header to arrive as several lines (RFC 7230 §3.2.2),
# equivalent to one line of all the values comma-joined IN ARRIVAL ORDER. A
# proxy that does not consolidate passes a client's lines through and appends
# its own word after them — so the first line can be pure client claim while the
# honest chain sits on a later line. client_ip() must read them ALL.


def test_a_spoofed_first_xff_line_is_not_believed():
    """THE finding. Two XFF lines arrive through the trusted proxy: the first is
    the client's spoofed tailnet claim, the second is the proxy's word for the
    real (outside) client. Reading line 1 alone granted the admin zone; the walk
    must run over the concatenation of every line and stop at the proxy's word."""
    req = _Req(PROXY, [("x-forwarded-for", INSIDE), ("x-forwarded-for", OUTSIDE)])
    s = Settings(admin_networks=[TAILNET], trusted_proxies=[f"{PROXY}/32"])
    assert client_ip(req, s.trusted_proxies) == OUTSIDE
    assert not is_admin_zone(req, s), "a spoofed first XFF line resolved inside the admin zone"


def test_a_spoofed_first_line_among_many_is_still_not_believed():
    """The client may spray several lines; every one of them still lands to the
    LEFT of whatever the trusted proxy appends, and the walk never reaches them."""
    req = _Req(
        PROXY,
        [
            ("x-forwarded-for", INSIDE),
            ("x-forwarded-for", f"{INSIDE}, 192.168.20.10"),
            ("x-forwarded-for", OUTSIDE),   # the trusted proxy's append
        ],
    )
    s = Settings(admin_networks=[TAILNET], trusted_proxies=[f"{PROXY}/32"])
    assert client_ip(req, s.trusted_proxies) == OUTSIDE
    assert not is_admin_zone(req, s)


def test_a_legitimate_admin_request_survives_two_proxied_hops():
    """The fix must not over-correct: tailnet client -> proxy1 -> proxy2 -> panel,
    each hop appending on its OWN line. The concatenated chain walked from the
    right skips both trusted hops and still resolves the real tailnet client."""
    req = _Req(PROXY2, [("x-forwarded-for", INSIDE), ("x-forwarded-for", PROXY)])
    trusted = [f"{PROXY}/32", f"{PROXY2}/32"]
    s = Settings(admin_networks=[TAILNET], trusted_proxies=trusted)
    assert client_ip(req, trusted) == INSIDE
    assert is_admin_zone(req, s), "a real admin-zone client was lost across two proxied hops"


def test_empty_and_whitespace_xff_lines_fall_back_to_the_peer():
    """XFF present but carrying nothing — empty lines, blank entries — is no
    chain at all: the trusted peer's own address is the only truth left."""
    for lines in (
        [("x-forwarded-for", "")],
        [("x-forwarded-for", "   ")],
        [("x-forwarded-for", ""), ("x-forwarded-for", " , , ")],
    ):
        req = _Req(PROXY, lines)
        assert client_ip(req, [f"{PROXY}/32"]) == PROXY


def test_non_ip_entries_in_the_chain_cannot_break_the_walk():
    """Junk that is not an address is skipped; it must neither be resolved nor
    stop the walk from reaching the real client to its left."""
    req = _Req(PROXY, [("x-forwarded-for", OUTSIDE), ("x-forwarded-for", "not-an-ip")])
    assert client_ip(req, [f"{PROXY}/32"]) == OUTSIDE


# --- 2. admin cannot log in from outside -------------------------------------


def test_admin_login_is_refused_from_the_public_internet(anon, exposed, caplog):
    """Refused — and the refusal is INDISTINGUISHABLE from a wrong password.
    A distinct 403 here answered "was that the right password?" for anyone on
    the internet: verify guesses until the 403 becomes a session. The client
    gets the generic 401; the real reason goes to the server log + audit trail,
    where an operator (not an attacker) reads it."""
    import logging

    with caplog.at_level(logging.WARNING, logger="hlidskjalf"):
        r = anon.post(
            "/api/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
            headers=_as(anon, OUTSIDE),
        )
    assert r.status_code == 401
    assert r.json()["detail"] == "Bad username or password"
    assert not r.cookies  # and no session was issued
    # The refusal is NOT silent — it is logged server-side, with the username.
    assert any(
        "outside the admin networks" in rec.getMessage() and ADMIN_USER in rec.getMessage()
        for rec in caplog.records
    ), "the zone refusal vanished without a server-side trace"


def test_the_zone_refusal_is_identical_to_a_wrong_password(anon, exposed):
    """The oracle, directly: a VALID admin credential from outside must get the
    same answer as an INVALID one — same status, same body."""
    good = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, OUTSIDE),
    )
    bad = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": "not-the-password"},
        headers=_as(anon, OUTSIDE),
    )
    assert good.status_code == bad.status_code == 401
    assert good.json() == bad.json()


def test_the_zone_refusal_is_still_audited(anon, exposed):
    """Refusals must remain in the durable audit trail with the real reason —
    hiding the 403 from the attacker must not hide it from the operator."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, OUTSIDE),
    )
    assert r.status_code == 401
    # Sign in from INSIDE to read the audit log back.
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200, r.text
    audit = anon.get(
        "/api/debug/audit",
        params={"action": "auth.login_failed"},
        headers=_as(anon, INSIDE),
    ).json()
    assert any(
        "outside the admin networks" in (e.get("detail") or "") for e in audit
    ), "the out-of-zone admin login attempt was not audited"


def test_spoofed_cf_connecting_ip_does_not_grant_admin(anon, exposed):
    """The netzone finding, end to end: not behind Cloudflare (the default), a caller
    must not forge admin-zone membership with CF-Connecting-IP. A public request that
    names a tailnet address in that header is still an outside request and is refused."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers={"X-Forwarded-For": OUTSIDE, "CF-Connecting-IP": INSIDE},
    )
    assert r.status_code == 401, "spoofed CF-Connecting-IP was believed; admin login allowed from outside"
    assert not r.cookies


def test_a_spoofed_first_xff_line_does_not_grant_admin(anon, exposed):
    """The duplicate-XFF finding, end to end through the real ASGI header parsing:
    the request carries TWO X-Forwarded-For lines, the first a spoofed tailnet
    address, the second the proxy's word for the real (outside) client. Before
    the fix only the first line was read — and this login returned 200."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=[("X-Forwarded-For", INSIDE), ("X-Forwarded-For", OUTSIDE)],
    )
    assert r.status_code == 403, "a spoofed first XFF line was believed; admin login allowed from outside"
    assert not r.cookies


def test_admin_login_works_across_two_proxied_hops(anon, exposed, monkeypatch):
    """Tunnel in front of a local reverse proxy: two trusted hops, each appending
    its own XFF line. A tailnet admin must still be recognised — the chain spans
    lines, and the right-to-left walk skips both trusted hops."""
    monkeypatch.setattr(exposed, "trusted_proxies", [f"{PROXY}/32", f"{PROXY2}/32"])
    monkeypatch.setattr(anon._transport, "client", (PROXY2, 50000))
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=[("X-Forwarded-For", INSIDE), ("X-Forwarded-For", PROXY)],
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"


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


def test_the_session_check_itself_refuses_an_out_of_zone_admin(anon, exposed):
    """/api/session is the liveness endpoint the SPA trusts, and it hands back
    role + a CSRF token. It used to skip the zone boundary (require_session_full
    has no deny_admin_outside_zone), so an admin cookie from the internet looked
    alive there while every real route refused it."""
    r = anon.post(
        "/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        headers=_as(anon, INSIDE),
    )
    assert r.status_code == 200, r.text
    assert anon.get("/api/session", headers=_as(anon, INSIDE)).status_code == 200

    r = anon.get("/api/session", headers=_as(anon, OUTSIDE))
    assert r.status_code == 403, (
        "an admin cookie from the internet passed the session liveness check"
    )


def test_the_session_check_still_works_for_a_tenant_from_anywhere(auth_client, anon, exposed):
    """Only admin is zoned. A tenant's /api/session must answer from anywhere —
    that is the whole point of exposing the panel."""
    r = auth_client.post(
        "/api/users",
        json={"username": "roamer", "password": "roamer-pass1", "role": "user"},
        headers={**csrf_headers(auth_client), **_as(auth_client, INSIDE)},
    )
    assert r.status_code in (201, 409), r.text
    anon.cookies.clear()
    r = anon.post(
        "/api/login",
        json={"username": "roamer", "password": "roamer-pass1"},
        headers=_as(anon, OUTSIDE),
    )
    assert r.status_code == 200, r.text
    assert anon.get("/api/session", headers=_as(anon, OUTSIDE)).status_code == 200


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
