"""Bandwidth accounting queries against the panel's own sqlite."""

import re
from calendar import monthrange
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user, is_admin, require_session
from ..db import Db
from ..deps import get_db, get_pve, settings
from ..pve import PveClient

router = APIRouter()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _current_month_bounds() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    last = monthrange(today.year, today.month)[1]
    return (
        date(today.year, today.month, 1).isoformat(),
        date(today.year, today.month, last).isoformat(),
    )


@router.get("/api/vms/{vmid}/bandwidth")
async def vm_bandwidth(
    vmid: int,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Db = Depends(get_db),
    pve: PveClient = Depends(get_pve),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user) and user.get("vmid") != vmid:
        raise HTTPException(403, "You do not have access to this VM's bandwidth")
    if from_ is None or to is None:
        from_, to = _current_month_bounds()
    if not DATE_RE.match(from_) or not DATE_RE.match(to):
        raise HTTPException(400, "from/to must be YYYY-MM-DD")
    days = await db.bandwidth_range(vmid, from_, to)
    t_in = sum(d["bytes_in"] for d in days)
    t_out = sum(d["bytes_out"] for d in days)
    quota_gb = settings().bandwidth_quotas.get(str(vmid))
    utilization = (t_in + t_out) / (quota_gb * 1024**3) if quota_gb else None
    return {
        "from": from_,
        "to": to,
        "days": days,
        "totals": {"bytes_in": t_in, "bytes_out": t_out, "total": t_in + t_out},
        "quota_gb": quota_gb,
        "utilization": utilization,
    }


@router.get("/api/vms/{vmid}/bandwidth/monthly")
async def vm_bandwidth_monthly(
    vmid: int,
    year: int | None = None,
    db: Db = Depends(get_db),
    pve: PveClient = Depends(get_pve),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user) and user.get("vmid") != vmid:
        raise HTTPException(403, "You do not have access to this VM's bandwidth")
    year = year or datetime.now(timezone.utc).year
    rows = {r["month"]: r for r in await db.bandwidth_monthly(vmid, year)}
    months = []
    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        r = rows.get(key)
        months.append({
            "month": m,
            "bytes_in": r["bytes_in"] if r else 0,
            "bytes_out": r["bytes_out"] if r else 0,
        })
    return {"year": year, "months": months}


@router.get("/api/bandwidth/summary")
async def bandwidth_summary(
    month: str | None = None,
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    if not MONTH_RE.match(month):
        raise HTTPException(400, "month must be YYYY-MM")
    rows = await db.bandwidth_summary(month)
    return {
        "month": month,
        "vms": {
            str(r["vmid"]): {
                "bytes_in": r["bytes_in"],
                "bytes_out": r["bytes_out"],
                "total": r["bytes_in"] + r["bytes_out"],
            }
            for r in rows
        },
    }
