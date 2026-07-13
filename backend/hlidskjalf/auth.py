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
# Per-client-IP login attempt buckets. Keyed by client IP so one attacker cannot
# lock out every other client (a single global counter was a trivial DoS).
_login_attempts: dict[str, deque[float]] = {}

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


def check_login_rate(client_ip: str) -> None:
    """Per-IP login rate limit: LOGIN_RATE attempts / LOGIN_WINDOW seconds.

    Keyed by client IP so a single abusive source cannot lock everyone else out.
    Empty (fully-expired) buckets are pruned each call to bound memory.
    """
    now = time.monotonic()
    # Prune expired timestamps everywhere and drop empty buckets (cleanup).
    for ip in list(_login_attempts.keys()):
        dq = _login_attempts[ip]
        while dq and now - dq[0] > LOGIN_WINDOW:
            dq.popleft()
        if not dq:
            del _login_attempts[ip]

    dq = _login_attempts.setdefault(client_ip, deque())
    if len(dq) >= LOGIN_RATE:
        raise HTTPException(429, "Too many login attempts, wait a minute")
    dq.append(now)


def reset_login_rate() -> None:
    """Clear all per-IP login buckets (test/ops helper)."""
    _login_attempts.clear()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


# A real argon2 hash, verified against when the username does not exist so that
# "no such user" costs the same wall-clock time as "wrong password". Without it,
# login latency is a username-enumeration oracle.
_DUMMY_HASH = _hasher.hash("hlidskjalf-timing-equalisation-dummy")


async def verify_user(db: Db, username: str, password: str) -> dict | None:
    """Return user dict (without hash) if password matches, else None."""
    user = await db.get_user_by_username(username)
    if not user:
        # Burn the same argon2 work an existing user would (see _DUMMY_HASH).
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except VerifyMismatchError:
            pass
        return None
    try:
        if _hasher.verify(user["password_hash"], password):
            # strip sensitive
            return {k: v for k, v in user.items() if k != "password_hash"}
    except VerifyMismatchError:
        pass
    return None


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-ish password check against a stored argon2 hash."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


# --- Session binding ---------------------------------------------------------
#
# The session cookie carries "<username>|<epoch>", where epoch is derived from
# the user's *current* password hash. Changing a password changes the epoch,
# which invalidates every session issued under the old password — otherwise a
# stolen cookie would outlive the password reset meant to evict it.


def session_epoch(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


async def current_epoch(username: str, db: Db) -> str:
    """The epoch a valid session for `username` must currently carry."""
    user = await db.get_user_by_username(username)
    if user:
        return session_epoch(user["password_hash"])
    # Fresh-bootstrap env admin (no DB row yet).
    s = get_settings()
    if s.admin_password_hash and secrets.compare_digest(username, s.admin_user):
        return session_epoch(s.admin_password_hash)
    raise HTTPException(401, "User no longer exists")


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


def start_session(response: Response, username: str, epoch: str) -> str:
    """Set the session cookie ("<username>|<epoch>"); return the CSRF token.

    `epoch` binds the session to the password it was issued under — get it from
    `current_epoch(username, db)`.
    """
    signed = _signer().sign(f"{username}|{epoch}").decode()
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=get_settings().session_max_age,
        httponly=True,
        samesite="strict",
        secure=get_settings().cookie_secure,
        path="/",
    )
    # CSRF must be derived from the same value require_csrf / /api/session use,
    # which is the *username*. Deriving it from the signed cookie here would hand
    # out a token that never matches on mutations.
    return csrf_for(username)


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _unsign(cookie: str | None) -> tuple[str, str]:
    """Returns (username, epoch) from the signed session cookie."""
    if not cookie:
        raise HTTPException(401, "Not logged in")
    try:
        raw = _signer().unsign(cookie, max_age=get_settings().session_max_age).decode()
    except BadSignature:
        raise HTTPException(401, "Session invalid or expired")
    username, sep, epoch = raw.partition("|")
    if not sep or not epoch:
        # Pre-epoch cookie format (or tampering): refuse it. Upgrading the panel
        # therefore forces one fresh login, which is the intended behaviour.
        raise HTTPException(401, "Session invalid or expired")
    return username, epoch


async def validate_session(cookie: str | None, db: Db) -> str:
    """Unsign the cookie and confirm it was issued under the *current* password."""
    username, epoch = _unsign(cookie)
    expected = await current_epoch(username, db)
    if not hmac.compare_digest(epoch, expected):
        raise HTTPException(401, "Session expired — password changed")
    return username


async def require_session(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Db = Depends(get_db),
) -> str:
    """Dependency: returns the username from a valid, current session cookie."""
    return await validate_session(session, db)


async def require_csrf(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias=CSRF_HEADER),
    db: Db = Depends(get_db),
) -> str:
    """Mutating: validates the session + CSRF and returns username."""
    username = await validate_session(session, db)
    if not csrf or not hmac.compare_digest(csrf, csrf_for(username)):
        raise HTTPException(403, f"Missing or bad {CSRF_HEADER} header")
    return username


async def session_from_request(request: Request, db: Db) -> str:
    """WebSocket: returns username from a valid, current session cookie."""
    return await validate_session(request.cookies.get(COOKIE_NAME), db)


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
