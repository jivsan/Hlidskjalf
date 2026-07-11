"""VM list, detail, and power actions."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_csrf, require_session
from ..db import Db
from ..deps import get_db, get_pve, guard_protected, settings
from ..pve import PveClient, PveError

router = APIRouter()

PowerAction = Literal["start", "shutdown", "reboot", "stop", "reset"]
DESTRUCTIVE_POWER = {"stop", "reset"}


@router.get("/api/vms")
async def list_vms(
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    _=Depends(require_session),
):
    resources = await pve.cluster_resources()
    rescued = set(await db.rescue_all())
    out = []
    for r in resources:
        if r.get("template") == 1:
            continue
        out.append({
            "vmid": r.get("vmid"),
            "name": r.get("name"),
            "kind": pve.guest_kind(r),
            "status": r.get("status"),
            "cpu": r.get("cpu"),
            "maxcpu": r.get("maxcpu"),
            "mem": r.get("mem"),
            "maxmem": r.get("maxmem"),
            "disk": r.get("disk"),
            "maxdisk": r.get("maxdisk"),
            "uptime": r.get("uptime"),
            "netin": r.get("netin"),
            "netout": r.get("netout"),
            "tags": r.get("tags"),
            "protected": r.get("vmid") in settings().protected_vmids,
            "rescue": r.get("vmid") in rescued,
        })
    out.sort(key=lambda v: v["vmid"] or 0)
    return out


def _parse_net0(net0: str) -> dict:
    """'virtio=BC:24:...,bridge=vmbr0,tag=20,firewall=0' → parts."""
    parts: dict[str, str] = {}
    for chunk in (net0 or "").split(","):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip()] = v.strip()
    return parts


@router.get("/api/vms/{vmid}")
async def vm_detail(
    vmid: int,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    _=Depends(require_session),
):
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    kind = pve.guest_kind(resource)
    current = await pve.vm_current(vmid, kind)
    config = await pve.vm_config(vmid, kind)

    ips: list[str] = []
    if kind == "qemu" and current.get("status") == "running" and current.get("agent"):
        try:
            agent = await pve.get(f"/nodes/{pve.node}/qemu/{vmid}/agent/network-get-interfaces")
            for iface in (agent or {}).get("result", []):
                if iface.get("name") in ("lo",):
                    continue
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ips.append(addr["ip-address"])
        except PveError:
            pass  # agent not responding — fall back to config below
    if not ips:
        ipconfig = config.get("ipconfig0", "")
        for chunk in ipconfig.split(","):
            if chunk.startswith("ip=") and "/" in chunk:
                ips.append(chunk[3:].split("/")[0])

    net0 = _parse_net0(config.get("net0", ""))
    rescue = await db.rescue_get(vmid)
    return {
        "vmid": vmid,
        "name": current.get("name") or resource.get("name"),
        "kind": kind,
        "status": current.get("status"),
        "uptime": current.get("uptime"),
        "cpu": current.get("cpu"),
        "maxcpu": current.get("cpus") or resource.get("maxcpu"),
        "mem": current.get("mem"),
        "maxmem": current.get("maxmem"),
        "disk": current.get("disk") or resource.get("disk"),
        "maxdisk": current.get("maxdisk") or resource.get("maxdisk"),
        "netin": current.get("netin"),
        "netout": current.get("netout"),
        "diskread": current.get("diskread"),
        "diskwrite": current.get("diskwrite"),
        "agent": bool(current.get("agent")),
        "ips": ips,
        "vlan": net0.get("tag"),
        "mac": next((v for k, v in net0.items() if k in ("virtio", "e1000", "vmxnet3", "rtl8139")), None),
        "bridge": net0.get("bridge"),
        "config": {
            "cores": config.get("cores"),
            "memory": config.get("memory"),
            "onboot": config.get("onboot"),
            "boot": config.get("boot"),
            "ostype": config.get("ostype"),
            "description": config.get("description"),
        },
        "protected": vmid in settings().protected_vmids,
        "rescue": rescue is not None,
        "rescue_since": rescue["entered_at"] if rescue else None,
    }


@router.post("/api/vms/{vmid}/status/{action}")
async def power_action(
    vmid: int,
    action: PowerAction,
    pve: PveClient = Depends(get_pve),
    _=Depends(require_csrf),
):
    if action in DESTRUCTIVE_POWER:
        guard_protected(vmid, action)
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    kind = pve.guest_kind(resource)
    if kind == "lxc" and action == "reset":
        raise HTTPException(400, "LXC containers do not support reset")
    upid = await pve.post(f"/nodes/{pve.node}/{kind}/{vmid}/status/{action}")
    return {"upid": upid}


@router.get("/api/tasks/{upid}/status")
async def upid_status(upid: str, pve: PveClient = Depends(get_pve), _=Depends(require_session)):
    return await pve.task_status(upid)


@router.get("/api/tasks/recent")
async def recent_tasks(pve: PveClient = Depends(get_pve), _=Depends(require_session)):
    tasks = await pve.get(f"/nodes/{pve.node}/tasks", limit=50)
    return tasks or []
