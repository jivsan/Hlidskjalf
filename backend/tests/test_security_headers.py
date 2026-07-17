"""The transport/anti-clickjacking headers must never silently regress.

The login page is internet-facing for tenants: if `frame-ancestors` or
`X-Frame-Options` ever disappeared, a lookalike site could frame the real login
and harvest credentials — the classic clickjacking phish. The CSP is also the
second line of defense behind React's escaping: pin every header the middleware
promises, on an API route and on the SPA path, plus the cache and HSTS rules.
"""

from hlidskjalf.config import get_settings


def test_security_headers_on_an_api_route(anon):
    r = anon.get("/api/setup/status")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "geolocation=()" in r.headers["permissions-policy"]
    # API answers carry tenant data (and the console ticket) — never cacheable.
    assert r.headers["cache-control"] == "no-store"


def test_security_headers_on_the_spa_path(anon):
    """The clickjacking defense must ride every non-API response — including
    errors, since the SPA path 404s when no build is present in the test env."""
    r = anon.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_hsts_only_when_the_deployment_is_tls(anon):
    # The suite runs http (cookie_secure=false): HSTS must stay off.
    r = anon.get("/api/setup/status")
    assert "strict-transport-security" not in r.headers
    # Behind TLS it is required — and must come back on.
    object.__setattr__(get_settings(), "cookie_secure", True)
    try:
        r2 = anon.get("/api/setup/status")
        assert "max-age=31536000" in r2.headers["strict-transport-security"]
    finally:
        object.__setattr__(get_settings(), "cookie_secure", False)
