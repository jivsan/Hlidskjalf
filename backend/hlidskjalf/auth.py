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

from . import netzone
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

# Generic buckets for everything else. Keyed "<action>:<who>" — usually the
# username, so one tenant hammering the API cannot throttle everybody else.
# Until now only login was limited: a compromised session could fire destroy or
# provision in a loop and drive the whole Proxmox API into the ground.
_action_buckets: dict[str, deque[float]] = {}


def check_rate(bucket: str, limit: int, window: float) -> None:
    """Sliding-window rate limit. Raises 429 when `bucket` exceeds `limit`/`window`."""
    now = time.monotonic()
    for key in list(_action_buckets.keys()):
        dq = _action_buckets[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if not dq:
            del _action_buckets[key]

    dq = _action_buckets.setdefault(bucket, deque())
    if len(dq) >= limit:
        raise HTTPException(429, "Too many requests — slow down")
    dq.append(now)


def reset_rates() -> None:
    """Clear every action bucket (test/ops helper)."""
    _action_buckets.clear()


def _signer() -> TimestampSigner:
    secret = get_settings().session_secret
    if not secret:
        raise HTTPException(500, "HLIDSKJALF_SESSION_SECRET not configured")
    return TimestampSigner(secret, salt="hlidskjalf-session")


def csrf_for(username: str, epoch: str = "") -> str:
    """CSRF token for a session.

    Bound to the password epoch as well as the username, so the token rotates
    whenever the password does. Deriving it from the username alone made it a
    permanent constant: leak it once (a log, a screenshot, an XSS) and it stayed
    valid forever.
    """
    key = get_settings().session_secret.encode()
    return hmac.new(key, f"csrf:{username}|{epoch}".encode(), hashlib.sha256).hexdigest()


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


# Failed attempts per ACCOUNT, not per source. Per-IP limiting is the right defence
# against one abusive host, and no defence at all against a botnet spreading attempts
# across thousands of addresses — which is exactly what an internet-facing login page
# attracts. So the account itself backs off too.
_failed_by_user: dict[str, deque] = {}
ACCOUNT_FAILURES = 10          # within...
ACCOUNT_WINDOW = 900.0         # ...15 minutes -> the account stops answering
ACCOUNT_LOCKOUT = 900.0        # for 15 minutes


def check_account_lockout(username: str) -> None:
    """Refuse an account that has been guessed at too hard, wherever from.

    Deliberately NOT a permanent lock: a permanent one is a denial-of-service any
    stranger can inflict on you by typing your username wrong ten times. It expires.
    """
    now = time.monotonic()
    dq = _failed_by_user.get(username)
    if not dq:
        return
    while dq and now - dq[0] > ACCOUNT_WINDOW:
        dq.popleft()
    if not dq:
        del _failed_by_user[username]
        return
    if len(dq) >= ACCOUNT_FAILURES:
        wait = int((ACCOUNT_LOCKOUT - (now - dq[-1])) / 60) + 1
        raise HTTPException(
            429,
            f"Too many failed sign-ins for this account. Try again in about {wait} minute(s).",
        )


def record_login_failure(username: str) -> None:
    now = time.monotonic()
    dq = _failed_by_user.setdefault(username, deque())
    while dq and now - dq[0] > ACCOUNT_WINDOW:
        dq.popleft()
    dq.append(now)


def clear_login_failures(username: str) -> None:
    """A successful sign-in clears the account's backoff."""
    _failed_by_user.pop(username, None)


def reset_login_rate() -> None:
    """Clear all per-IP login buckets and per-account backoff (test/ops helper)."""
    _login_attempts.clear()
    _failed_by_user.clear()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def new_session_secret() -> str:
    """A fresh signing secret, minted on first run when none was configured."""
    return secrets.token_urlsafe(48)


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
    """Set the session cookie ("<username>|<epoch>|<sid>"); return the CSRF token.

    `epoch` binds the session to the password it was issued under (a password
    change invalidates it). `sid` identifies this one session so that logging out
    can revoke *it* — a signed stateless cookie is otherwise valid until it
    expires, no matter how many times you press "log out".
    """
    sid = secrets.token_urlsafe(16)
    signed = _signer().sign(f"{username}|{epoch}|{sid}").decode()
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=get_settings().session_max_age,
        httponly=True,
        samesite="strict",
        secure=get_settings().cookie_secure,
        path="/",
    )
    return csrf_for(username, epoch)


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _unsign(cookie: str | None) -> tuple[str, str, str]:
    """Returns (username, epoch, sid) from the signed session cookie."""
    if not cookie:
        raise HTTPException(401, "Not logged in")
    try:
        raw = _signer().unsign(cookie, max_age=get_settings().session_max_age).decode()
    except BadSignature:
        raise HTTPException(401, "Session invalid or expired")
    parts = raw.split("|")
    if len(parts) != 3 or not all(parts):
        # An older cookie format (or tampering): refuse it. Upgrading the panel
        # therefore forces one fresh login, which is the intended behaviour.
        raise HTTPException(401, "Session invalid or expired")
    return parts[0], parts[1], parts[2]


async def validate_session(cookie: str | None, db: Db) -> tuple[str, str, str]:
    """Full session check. Returns (username, epoch, sid).

    A session is valid only if it is signed, was issued under the user's *current*
    password, and has not been revoked by a logout.
    """
    username, epoch, sid = _unsign(cookie)
    expected = await current_epoch(username, db)
    if not hmac.compare_digest(epoch, expected):
        raise HTTPException(401, "Session expired — password changed")
    if await db.is_session_revoked(sid):
        raise HTTPException(401, "Session was logged out")
    return username, epoch, sid


async def require_session(
    request: Request,
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Db = Depends(get_db),
) -> str:
    """Dependency: returns the username from a valid, current session cookie."""
    username, _epoch, _sid = await validate_session(session, db)
    await deny_admin_outside_zone(request, username, db)
    return username


async def require_session_full(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Db = Depends(get_db),
) -> tuple[str, str, str]:
    """Like `require_session`, but hands back (username, epoch, sid) — used by logout."""
    return await validate_session(session, db)


async def require_csrf(
    request: Request,
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias=CSRF_HEADER),
    db: Db = Depends(get_db),
) -> str:
    """Mutating: validates the session + CSRF and returns username."""
    username, epoch, _sid = await validate_session(session, db)
    if not csrf or not hmac.compare_digest(csrf, csrf_for(username, epoch)):
        raise HTTPException(403, f"Missing or bad {CSRF_HEADER} header")
    await deny_admin_outside_zone(request, username, db)
    return username


async def session_from_request(request: Request, db: Db) -> str:
    """WebSocket: returns username from a valid, current session cookie.

    The console websocket goes through here, so an admin's console from outside the
    admin networks is refused too — the tenant's own console still works, which is
    the whole point of exposing the panel.
    """
    username, _epoch, _sid = await validate_session(request.cookies.get(COOKIE_NAME), db)
    await deny_admin_outside_zone(request, username, db)
    return username


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


async def deny_admin_outside_zone(request: Request, username: str, db: Db) -> None:
    """An admin session must not work outside the admin networks. At all.

    Not "must not see the admin pages" — must not *work*. A session cookie travels
    with the browser: sign in as admin at home over Tailscale, open the same laptop
    on café wifi, and the cookie is still valid. `require_admin_user` would refuse
    it, but ~20 routes branch on `is_admin(user)` directly (fleet scoping, power,
    provisioning) and would have honoured it. So the check belongs at the session
    layer, where every authenticated path passes through exactly once.

    Free when `admin_networks` is unset (a LAN-only panel) or the caller is inside
    it: the user lookup only happens for a session arriving from outside.
    """
    s = get_settings()
    if not s.admin_networks or netzone.is_admin_zone(request, s):
        return
    user = await db.get_user_by_username(username)
    role = (user or {}).get("role") or (
        "admin" if secrets.compare_digest(username, s.admin_user) else "user"
    )
    if role == "admin":
        raise HTTPException(403, netzone.admin_zone_error(s))


def is_admin(user: dict) -> bool:
    return (user or {}).get("role") == "admin"


def rate_limited(action: str, limit: int, window: float):
    """Dependency factory: throttle `action` per user.

    Keyed on the username, so one tenant hammering destroy cannot throttle anyone
    else — and cannot drive the Proxmox API into the ground on their own either.
    """

    async def dep(username: str = Depends(require_session)) -> str:
        check_rate(f"{action}:{username}", limit, window)
        return username

    return dep


async def require_admin_user(
    request: Request,
    username: str = Depends(require_session),
    db: Db = Depends(get_db),
) -> dict:
    """Dependency: returns the admin user dict, or 403.

    TWO refusals, not one. Being an admin is not enough — the request must also come
    from a network the operator allows admin from (`admin_networks`, empty = anywhere).
    The panel is reachable from the internet so tenants can manage their own VM; admin
    must not be reachable there, and hiding the URL is not a boundary.
    """
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    settings = get_settings()
    if not netzone.is_admin_zone(request, settings):
        raise HTTPException(403, netzone.admin_zone_error(settings))
    return user
