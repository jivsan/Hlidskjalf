"""Multi-user auth with roles (admin / user).

Each regular user is assigned exactly one VM (VPS model).
Admins see everything and can manage users + provision.

Session value is now the *username* (signed). We validate the user still exists on use.
"""

import hashlib
import hmac
import secrets
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner

from .config import get_settings
from .db import Db  # for type hints only
from .deps import get_db

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


def hash_password(password: str) -> str:
    return _hasher.hash(password)


async def verify_user(db: Db, username: str, password: str) -> dict | None:
    """Return user dict (without hash) if password matches, else None."""
    user = await db.get_user_by_username(username)
    if not user:
        return None
    try:
        if _hasher.verify(user["password_hash"], password):
            # strip sensitive
            return {k: v for k, v in user.items() if k != "password_hash"}
    except VerifyMismatchError:
        pass
    return None


# Back-compat for very first bootstrap before DB users exist (dev)
def _legacy_verify(username: str, password: str) -> bool:
    s = get_settings()
    if not s.admin_password_hash:
        return False
    if not secrets.compare_digest(username, s.admin_user):
        return False
    try:
        return _hasher.verify(s.admin_password_hash, password)
    except VerifyMismatchError:
        return False


def start_session(response: Response, username: str) -> str:
    """Set the session cookie containing the username; return the CSRF token."""
    # We sign the username directly. On use we re-validate against DB.
    signed = _signer().sign(username).decode()
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=get_settings().session_max_age,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    # CSRF must be derived from the same value require_csrf / /api/session use,
    # which is the *username* (the unsigned cookie value). Deriving it from the
    # signed cookie here would hand out a token that never matches on mutations.
    return csrf_for(username)


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _unsign(cookie: str | None) -> str:
    """Returns the username stored in the signed session cookie."""
    if not cookie:
        raise HTTPException(401, "Not logged in")
    try:
        return _signer().unsign(cookie, max_age=get_settings().session_max_age).decode()
    except BadSignature:
        raise HTTPException(401, "Session invalid or expired")


async def require_session(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> str:
    """Dependency: returns the username from the session cookie."""
    return _unsign(session)


async def require_csrf(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias=CSRF_HEADER),
) -> str:
    """Mutating: validates CSRF and returns username."""
    value = _unsign(session)
    if not csrf or not hmac.compare_digest(csrf, csrf_for(value)):
        raise HTTPException(403, f"Missing or bad {CSRF_HEADER} header")
    return value


def session_from_request(request: Request) -> str:
    """WebSocket: returns username."""
    return _unsign(request.cookies.get(COOKIE_NAME))


# --- User + role helpers (call from handlers: user = await get_current_user(username, db)) ---

async def get_current_user(username: str, db: Db) -> dict:
    """Load user record (role + assigned vmid). Falls back for legacy bootstrap."""
    user = await db.get_user_by_username(username)
    if user:
        return {k: v for k, v in user.items() if k != "password_hash"}
    s = get_settings()
    if secrets.compare_digest(username, s.admin_user):
        return {"username": username, "role": "admin", "vmid": None}
    raise HTTPException(401, "User no longer exists")


def is_admin(user: dict) -> bool:
    return (user or {}).get("role") == "admin"


async def require_admin_user(
    username: str = Depends(require_session),
    db: Db = Depends(get_db),
) -> dict:
    """Dependency: returns the admin user dict or raises 403 for non-admins.

    Works with multi-user admin checks (role == 'admin').
    """
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    return user
