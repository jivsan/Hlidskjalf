"""FastAPI dependencies pulling shared clients off app.state."""

from fastapi import HTTPException, Request

from .config import Settings, get_settings
from .db import Db
from .pve import PveClient
from .switch import AristaClient, get_switch_client


def get_pve(request: Request) -> PveClient:
    pve = request.app.state.pve
    if pve is None:
        # Unconfigured panel (fresh install, setup wizard not finished yet).
        raise HTTPException(503, "Proxmox is not configured yet — finish setup first")
    return pve


def get_db(request: Request) -> Db:
    return request.app.state.db


def get_metrics(request: Request):
    return request.app.state.metrics


def get_switch(request: Request) -> AristaClient:
    # Lazy - client is cheap to instantiate
    return get_switch_client()


def settings() -> Settings:
    return get_settings()


def guard_protected(vmid: int, action: str) -> None:
    """Server-side refusal of destructive actions on protected VMIDs."""
    if vmid in get_settings().protected_vmids:
        raise HTTPException(
            403,
            f"VMID {vmid} is protected — '{action}' is refused server-side "
            f"(HLIDSKJALF_PROTECTED_VMIDS)",
        )
