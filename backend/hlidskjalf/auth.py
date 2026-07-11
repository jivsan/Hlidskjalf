"""Single-user auth: argon2 password verify, signed session cookie, CSRF.

Session: itsdangerous-signed value in an HttpOnly SameSite=Strict cookie.
CSRF: an HMAC derived from the session value is returned to the SPA at login
(and from GET /api/session); every mutating request must echo it in the
X-Hlidskjalf-CSRF header. The cookie is HttpOnly so a same-site injection
can't read it to forge the header.
"""

import hashlib
import hmac
import secrets
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Header, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner

from .config import get_settings

COOKIE_NAME = "hlidskjalf_session"
CSRF_HEADER = "X-Hlidskjalf-CSRF"

_hasher = PasswordHasher()
_login_attempts: deque[float] = deque(maxlen=64)

LOGIN_RATE = 5          # attempts
LOGIN_WINDOW = 60.0     # seconds


def _signer() -> TimestampSigner:
    secret = get_settings().session_secret
    if not secret:
        raise HTTPException(500, "HLIDSKJALF_SESSION_SECRET not configured")
    return TimestampSigner(secret, salt="hlidskjalf-session")


def csrf_for(session_value: str) -> str:
    key = get_settings().session_secret.encode()
    return hmac.new(key, f"csrf:{session_value}".encode(), hashlib.sha256).hexdigest()


def check_login_rate() -> None:
    now = time.monotonic()
    while _login_attempts and now - _login_attempts[0] > LOGIN_WINDOW:
        _login_attempts.popleft()
    if len(_login_attempts) >= LOGIN_RATE:
        raise HTTPException(429, "Too many login attempts, wait a minute")
    _login_attempts.append(now)


def verify_password(username: str, password: str) -> bool:
    s = get_settings()
    if not s.admin_password_hash:
        raise HTTPException(500, "HLIDSKJALF_ADMIN_PASSWORD_HASH not configured")
    if not secrets.compare_digest(username, s.admin_user):
        return False
    try:
        return _hasher.verify(s.admin_password_hash, password)
    except VerifyMismatchError:
        return False


def start_session(response: Response) -> str:
    """Set the session cookie; return the CSRF token for the SPA."""
    value = secrets.token_urlsafe(32)
    signed = _signer().sign(value).decode()
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=get_settings().session_max_age,
        httponly=True,
        samesite="strict",
        secure=False,  # TLS terminates at Traefik; cookie never leaves the LAN
        path="/",
    )
    return csrf_for(value)


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _unsign(cookie: str | None) -> str:
    if not cookie:
        raise HTTPException(401, "Not logged in")
    try:
        return _signer().unsign(cookie, max_age=get_settings().session_max_age).decode()
    except BadSignature:
        raise HTTPException(401, "Session invalid or expired")


async def require_session(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> str:
    """Dependency for read endpoints; returns the raw session value."""
    return _unsign(session)


async def require_csrf(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias=CSRF_HEADER),
) -> str:
    """Dependency for mutating endpoints: session + matching CSRF header."""
    value = _unsign(session)
    if not csrf or not hmac.compare_digest(csrf, csrf_for(value)):
        raise HTTPException(403, f"Missing or bad {CSRF_HEADER} header")
    return value


def session_from_request(request: Request) -> str:
    """For WebSocket handshakes (no dependency injection of cookies there)."""
    return _unsign(request.cookies.get(COOKIE_NAME))
