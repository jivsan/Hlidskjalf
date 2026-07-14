"""Hlidskjalf app assembly: lifespan, auth endpoints, static SPA serving."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from .deps import get_db
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth, journal
from .accumulator import Accumulator
from .config import apply_stored, get_settings, unseal

# Always import debug router (endpoints are admin-only protected via require_admin_user).
# The debug features (buffers, verbose errors) activate when settings.debug or log_level=DEBUG.
from .routes import debug as debug_module
from .datasources.rrd import RRDSource
from .db import Db
from .pve import PveClient, PveError
from .routes import (
    bandwidth,
    console,
    metrics,
    provision,
    rescue,
    settings as settings_route,
    update as update_route,
    version as version_route,
    setup as setup_route,
    switch,
    users as users_route,
    vms,
)

log = logging.getLogger("hlidskjalf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Configure stdlib logging from settings.log_level (supports DEBUG/INFO/etc)
    level_name = (settings.log_level or "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # Attach in-memory log handler (for /api/debug/logs) when debug or DEBUG level
    if settings.debug or log_level <= logging.DEBUG:
        mem_handler = debug_module.InMemoryLogHandler()
        logging.getLogger("hlidskjalf").addHandler(mem_handler)

    app.state.db = Db(settings.db_path)
    await app.state.db.open()

    # Overlay any configuration written by the first-run setup wizard. Env always
    # wins (see config.apply_stored), so this cannot override an ops-managed deploy.
    # Stored secrets are encrypted at rest (secretbox.py) — decrypt on the way in.
    shadowed = apply_stored(settings, unseal(await app.state.db.get_config(), settings))
    for key, env_value, stored_value in shadowed:
        # The panel is about to use a value the operator did not choose here. This
        # is the rule working as designed (env wins, so an ops-managed deploy is
        # never overridden by the database) — but it is also how a NixOS module
        # default of pve_node="pve" silently overrode a wizard that had been told
        # "hella", and every node-scoped page then failed with a DNS error nobody
        # could trace back. Say it out loud, once, at startup.
        log.warning(
            "config: the environment sets %s=%r, overriding %r saved in the panel. "
            "The panel will use the environment value — unset HLIDSKJALF_%s to manage "
            "this from the UI.",
            key, env_value, stored_value, key.upper(),
        )

    # Bootstrap initial admin user from env if this is a fresh DB (dev + first remote deploy)
    await app.state.db.ensure_bootstrap_admin(settings.admin_user, settings.admin_password_hash)

    # An unconfigured panel must still boot — it has to serve the setup wizard.
    # (PveClient refuses https without a fingerprint, so constructing it here on a
    # fresh install would crash the app before anyone could configure it.)
    if settings.pve_host:
        await start_pve_stack(app, settings)
        log.info("hlidskjalf up — watching %s from the high seat", settings.pve_node)
        if not settings.protected_vmids:
            # The default is empty so the panel ships neutral rather than wired to
            # one homelab — but that means nothing is guarded, including the VM
            # this process is running on.
            log.warning(
                "HLIDSKJALF_PROTECTED_VMIDS is empty — NOTHING is protected. An admin "
                "can stop, reinstall or DESTROY any guest, including the VM running "
                "this panel. Set it to the VMIDs you cannot afford to lose."
            )
    else:
        app.state.pve = None
        app.state.metrics = None
        app.state.accumulator = None
        log.warning("Proxmox is not configured — serving the first-run setup wizard")
    try:
        yield
    finally:
        await stop_pve_stack(app)
        await app.state.db.close()


async def start_pve_stack(app: FastAPI, settings) -> None:
    """Build the PVE client, metrics source and accumulator for a configured panel.

    Called at startup, and again by the setup wizard once it commits a working
    connection — so a freshly-configured panel works without a restart.
    """
    app.state.pve = PveClient(settings)
    # Metrics datasource — rrd (PVE rrddata) is the default; prometheus is the
    # drop-in long-range alternative (same MetricsSource protocol, same rows).
    source = (settings.metrics_source or "rrd").lower()
    if source == "prometheus":
        from .datasources.prometheus import PrometheusSource

        # Raises a clear RuntimeError if prometheus_url is unset.
        app.state.metrics = PrometheusSource(settings)
        log.info("metrics source: prometheus (%s)", settings.prometheus_url)
    elif source == "rrd":
        app.state.metrics = RRDSource(app.state.pve)
    else:
        raise RuntimeError(
            f"HLIDSKJALF_METRICS_SOURCE={settings.metrics_source!r} is not valid "
            "(expected 'rrd' or 'prometheus')"
        )
    app.state.accumulator = Accumulator(app.state.pve, app.state.db)
    await app.state.accumulator.start()


async def stop_pve_stack(app: FastAPI) -> None:
    if getattr(app.state, "accumulator", None):
        await app.state.accumulator.stop()
    closer = getattr(app.state.metrics, "aclose", None)  # prometheus holds an httpx client
    if closer:
        await closer()
    if getattr(app.state, "pve", None):
        await app.state.pve.aclose()


app = FastAPI(title="Hlidskjalf", lifespan=lifespan)


# --- middleware: security headers ------------------------------------------

# The SPA is self-hosted and pulls nothing from third parties, so the policy can
# be strict. 'unsafe-inline' is needed for style-src only: Vite injects the
# stylesheet and a few components set inline style attributes (chart colours,
# meter widths). connect-src allows the same-origin WebSocket for the console.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'"
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")  # legacy peer of frame-ancestors
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    if get_settings().cookie_secure:
        # Only meaningful over TLS; the panel is expected to sit behind Traefik.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # API answers carry tenant data (and the console ticket) — never let a proxy
    # or the browser cache them.
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- middleware: request logging (method, path, status, duration, client) ---

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        client = request.client.host if request.client else "-"
        log.info(
            "request %s %s client=%s status=%s duration=%.1fms",
            request.method,
            request.url.path,
            client,
            status_code,
            duration_ms,
        )


# --- global exception handler (full traceback logs, consistent JSON) ---

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log full tracebacks at ERROR and return a GENERIC 500 to the client.

    The full entry (including the traceback and exception type) is recorded in
    the admin-only ``recent_errors`` buffer, surfaced via ``GET /api/debug/errors``.
    It is NEVER placed in the HTTP response body — not even when settings.debug is
    true — so that internals (tracebacks, exception types, messages) are never
    disclosed to clients, including unauthenticated ones.
    """
    client = request.client.host if request.client else "-"

    if isinstance(exc, HTTPException):
        if exc.status_code >= 500:
            log.error("HTTP %s error: %s %s -> %s", exc.status_code, request.method, request.url.path, exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # Unhandled: log with full traceback
    import traceback

    tb = traceback.format_exc()
    log.error(
        "Unhandled exception for %s %s (client=%s): %s",
        request.method,
        request.url.path,
        client,
        exc,
        exc_info=True,
    )

    # Record the full entry (with traceback) for admins only — never to the client.
    error_entry = {
        "ts": time.time(),
        "method": request.method,
        "path": request.url.path,
        "client": client,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "traceback": "\n".join(tb.strip().split("\n")[-20:]),
    }
    debug_module._append_recent(debug_module.recent_errors, error_entry)

    # Generic response for everyone, regardless of settings.debug.
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(PveError)
async def pve_error_handler(request: Request, exc: PveError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


# --- auth -----------------------------------------------------------------


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request, response: Response, db: Db = Depends(get_db)):
    client_ip = request.client.host if request.client else "-"
    auth.check_login_rate(client_ip)

    # Try DB user first
    user = await auth.verify_user(db, body.username, body.password)
    if not user:
        # Legacy env-hash fallback is ONLY valid during fresh bootstrap, i.e.
        # before any user row exists. Once real users are seeded (normal startup
        # runs ensure_bootstrap_admin), the env admin_password_hash is no longer
        # an accepted login path — otherwise a stale/weak env hash would be a
        # permanent backdoor even after the admin password is changed in the DB.
        if await db.list_users() or not auth._legacy_verify(body.username, body.password):
            await journal.record(
                db, request, body.username, journal.LOGIN_FAILED, ok=False
            )
            raise HTTPException(401, "Bad username or password")
        # On legacy success, make sure the bootstrap user exists now
        user = await db.get_user_by_username(body.username) or {
            "username": body.username,
            "role": "admin",
            "vmid": None,
        }

    # Bind the session to the password it was issued under, so a later password
    # change invalidates it (see auth.session_epoch).
    epoch = await auth.current_epoch(body.username, db)
    csrf = auth.start_session(response, body.username, epoch)
    await journal.record(db, request, body.username, journal.LOGIN)
    return {
        "ok": True,
        "csrf": csrf,
        "user": user["username"],
        "role": user.get("role", "admin"),
        "vmid": user.get("vmid"),
        "node": get_settings().pve_node,
    }


@app.post("/api/logout")
async def logout(
    request: Request,
    response: Response,
    session: tuple[str, str, str] = Depends(auth.require_session_full),
    db: Db = Depends(get_db),
):
    """Log out — and actually mean it.

    Deleting the cookie only asks the *browser* to forget it. A signed session
    cookie that someone else copied stayed valid until it expired, however many
    times you pressed log out. The session id is now parked in `revoked_sessions`
    until its natural expiry, so the cookie is dead everywhere.
    """
    username, _epoch, sid = session
    expires_at = time.time() + get_settings().session_max_age
    await db.revoke_session(sid, expires_at)
    await db.prune_revoked_sessions(time.time())
    auth.end_session(response)
    await journal.record(db, request, username, journal.LOGOUT)
    return {"ok": True}


@app.get("/api/session")
async def session(
    session: tuple[str, str, str] = Depends(auth.require_session_full),
    db: Db = Depends(get_db),
):
    username, epoch, _sid = session
    user = await auth.get_current_user(username, db)
    return {
        "user": user["username"],
        "role": user.get("role", "admin"),
        "vmid": user.get("vmid"),
        "csrf": auth.csrf_for(username, epoch),
        # The node the panel watches. The UI renders this instead of hardcoding a
        # host name, so any deployment shows its own node.
        "node": get_settings().pve_node,
    }


@app.get("/api/me")
async def me(value: str = Depends(auth.require_session), db: Db = Depends(get_db)):
    return await auth.get_current_user(value, db)


@app.get("/api/health")
async def health():
    return {"ok": True}


# --- feature routes ---------------------------------------------------------

app.include_router(vms.router)
app.include_router(metrics.router)
app.include_router(bandwidth.router)
app.include_router(provision.router)
app.include_router(rescue.router)
app.include_router(console.router)
app.include_router(switch.router)
app.include_router(users_route.router)  # new user management (admin only)
app.include_router(settings_route.router)  # /api/settings/* (admin only)
app.include_router(version_route.router)  # /api/version (admin only, fail-soft)
app.include_router(update_route.router)   # /api/update  (admin, off unless allowed)
app.include_router(setup_route.router)  # /api/setup/* — closes forever once a user exists

app.include_router(debug_module.router, prefix="/api/debug")  # /api/debug/* (admin protected)


# --- static SPA -------------------------------------------------------------

_static = get_settings().static_dir


if _static:
    static_root = Path(_static)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        candidate = (static_root / full_path).resolve()
        if (
            full_path
            and candidate.is_relative_to(static_root.resolve())
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(static_root / "index.html")


def run() -> None:
    """Console-script entrypoint (used by the Nix package)."""
    import os

    import uvicorn

    uvicorn.run(
        "hlidskjalf.main:app",
        host=os.environ.get("HLIDSKJALF_HOST", "127.0.0.1"),
        port=int(os.environ.get("HLIDSKJALF_PORT", "8787")),
        log_level="info",
    )


if __name__ == "__main__":
    run()
