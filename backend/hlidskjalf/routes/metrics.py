"""rrddata graphs (VM + node) and node status/storage."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user, is_admin, require_session
from ..db import Db
from ..deps import get_db, get_metrics, get_pve
from ..pve import PveClient

router = APIRouter()

Timeframe = Literal["hour", "day", "week", "month", "year"]


@router.get("/api/vms/{vmid}/metrics")
async def vm_metrics(
    vmid: int,
    timeframe: Timeframe = "hour",
    cf: Literal["AVERAGE", "MAX"] = "AVERAGE",
    pve: PveClient = Depends(get_pve),
    source=Depends(get_metrics),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user) and user.get("vmid") != vmid:
        raise HTTPException(403, "You do not have access to this VM's metrics")
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    return await source.get_vm_series(vmid, pve.guest_kind(resource), timeframe, cf)


@router.get("/api/node/metrics")
async def node_metrics(
    timeframe: Timeframe = "hour",
    cf: Literal["AVERAGE", "MAX"] = "AVERAGE",
    source=Depends(get_metrics),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    return await source.get_node_series(timeframe, cf)


@router.get("/api/node")
async def node_info(pve: PveClient = Depends(get_pve), db: Db = Depends(get_db), username: str = Depends(require_session)):
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    raw = await pve.get(f"/nodes/{pve.node}/status") or {}
    storage = await pve.get(f"/nodes/{pve.node}/storage")

    # Normalize PVE shape variations:
    # - Some responses have flat maxcpu/mem/maxmem
    # - Real PVE often nests memory under .memory and cpu cores under cpuinfo.cpus
    # Always provide flat fields the frontend prefers, while keeping raw nested data.
    mem_info = raw.get("memory") or {}
    cpuinfo = raw.get("cpuinfo") or {}
    normalized_status = {
        **raw,
        "maxcpu": raw.get("maxcpu") or cpuinfo.get("cpus"),
        "mem": raw.get("mem") or mem_info.get("used"),
        "maxmem": raw.get("maxmem") or mem_info.get("total"),
    }

    return {
        "name": pve.node,
        "status": normalized_status,
        "storage": [
            {
                "storage": s.get("storage"),
                "type": s.get("type"),
                "used": s.get("used"),
                "total": s.get("total"),
                "avail": s.get("avail"),
                "content": s.get("content"),
                "active": s.get("active"),
            }
            for s in (storage or [])
        ],
    }
