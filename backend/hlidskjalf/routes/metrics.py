"""rrddata graphs (VM + node) and node status/storage."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..deps import get_metrics, get_pve
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
    _=Depends(require_session),
):
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    return await source.get_vm_series(vmid, pve.guest_kind(resource), timeframe, cf)


@router.get("/api/node/metrics")
async def node_metrics(
    timeframe: Timeframe = "hour",
    cf: Literal["AVERAGE", "MAX"] = "AVERAGE",
    source=Depends(get_metrics),
    _=Depends(require_session),
):
    return await source.get_node_series(timeframe, cf)


@router.get("/api/node")
async def node_info(pve: PveClient = Depends(get_pve), _=Depends(require_session)):
    status = await pve.get(f"/nodes/{pve.node}/status")
    storage = await pve.get(f"/nodes/{pve.node}/storage")
    return {
        "name": pve.node,
        "status": status,
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
