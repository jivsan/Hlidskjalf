"""Hlidskjalf app assembly: lifespan, auth endpoints, static SPA serving."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth
from .accumulator import Accumulator
from .config import get_settings
from .datasources.rrd import RRDSource
from .db import Db
from .pve import PveClient, PveError
from .routes import bandwidth, console, metrics, provision, rescue, switch, vms

log = logging.getLogger("hlidskjalf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    app.state.db = Db(settings.db_path)
    await app.state.db.open()
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


@app.exception_handler(PveError)
async def pve_error_handler(request: Request, exc: PveError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


# --- auth -----------------------------------------------------------------


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LoginBody, response: Response):
    auth.check_login_rate()
    if not auth.verify_password(body.username, body.password):
        raise HTTPException(401, "Bad username or password")
    csrf = auth.start_session(response)
    return {"ok": True, "csrf": csrf}


@app.post("/api/logout")
async def logout(response: Response, _=Depends(auth.require_session)):
    auth.end_session(response)
    return {"ok": True}


@app.get("/api/session")
async def session(value: str = Depends(auth.require_session)):
    return {"user": get_settings().admin_user, "csrf": auth.csrf_for(value)}


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
