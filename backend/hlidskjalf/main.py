"""Hlidskjalf app assembly: lifespan, auth endpoints, static SPA serving."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from .deps import get_db
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth
from .accumulator import Accumulator
from .config import get_settings

# Always import debug router (endpoints are admin-only protected via require_admin_user).
# The debug features (buffers, verbose errors) activate when settings.debug or log_level=DEBUG.
from .routes import debug as debug_module
from .datasources.rrd import RRDSource
from .db import Db
from .pve import PveClient, PveError
from .routes import bandwidth, console, metrics, provision, rescue, switch, users as users_route, vms

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

    # Bootstrap initial admin user from env if this is a fresh DB (dev + first remote deploy)
    await app.state.db.ensure_bootstrap_admin(settings.admin_user, settings.admin_password_hash)

    app.state.pve = PveClient(settings)
    if settings.metrics_source != "rrd":
        from .datasources.prometheus import PrometheusSource

        app.state.metrics = PrometheusSource()
    else:
        app.state.metrics = RRDSource(app.state.pve)
    app.state.accumulator = Accumulator(app.state.pve, app.state.db)
    await app.state.accumulator.start()
    log.info("hlidskjalf up — watching %s from the high seat", settings.pve_node)
    try:
        yield
    finally:
        await app.state.accumulator.stop()
        await app.state.pve.aclose()
        await app.state.db.close()


app = FastAPI(title="Hlidskjalf", lifespan=lifespan)


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
            raise HTTPException(401, "Bad username or password")
        # On legacy success, make sure the bootstrap user exists now
        user = await db.get_user_by_username(body.username) or {
            "username": body.username,
            "role": "admin",
            "vmid": None,
        }

    csrf = auth.start_session(response, body.username)
    return {
        "ok": True,
        "csrf": csrf,
        "user": user["username"],
        "role": user.get("role", "admin"),
        "vmid": user.get("vmid"),
    }


@app.post("/api/logout")
async def logout(response: Response, _=Depends(auth.require_session)):
    auth.end_session(response)
    return {"ok": True}


@app.get("/api/session")
async def session(value: str = Depends(auth.require_session), db: Db = Depends(get_db)):
    user = await auth.get_current_user(value, db)
    return {
        "user": user["username"],
        "role": user.get("role", "admin"),
        "vmid": user.get("vmid"),
        "csrf": auth.csrf_for(value),
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
