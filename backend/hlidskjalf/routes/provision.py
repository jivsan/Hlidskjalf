"""Provisioning: template discovery, clone, reinstall, destroy.

Safety rails (non-negotiable, enforced here server-side):
- protected VMIDs refuse destroy/reinstall outright
- destroy/reinstall require confirm_name == exact VM name
- every net0 the panel ever writes hardcodes firewall=0 (VLAN tags silently
  break through the firewall bridge on hella with firewall=1 — fleet-wide bug).
  The bridge itself comes from settings.pve_bridge (admin-editable in Settings);
  only firewall=0 stays hardcoded.
"""

import re
import urllib.parse

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import journal
from pydantic import BaseModel, Field

from ..auth import get_current_user, is_admin, rate_limited, require_csrf, require_session
from ..deps import get_db, get_pve, guard_protected, settings
from ..db import Db
from ..pve import PveClient

router = APIRouter()

NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Where the panel starts looking when it picks a VMID itself. A VMID typed by an
# admin only has to clear Proxmox's own floor (100) — the panel does not get to
# reserve 100-199 for itself.
MIN_VMID = 200
PVE_MIN_VMID = 100
PVE_MAX_VMID = 999_999_999


def _net0(vlan: str | int, mac: str | None = None) -> str:
    model = f"virtio={mac}" if mac else "virtio"
    return f"{model},bridge={settings().pve_bridge},tag={vlan},firewall=0"


@router.get("/api/templates")
async def list_templates(
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    resources = await pve.cluster_resources()
    return [
        {"vmid": r["vmid"], "name": r.get("name")}
        for r in resources
        if r.get("template") == 1 and r.get("type") == "qemu"
    ]


@router.get("/api/provision/defaults")
async def provision_defaults(
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    user = await get_current_user(username, db)
    if not is_admin(user):
        raise HTTPException(403, "Admin only")
    s = settings()
    resources = await pve.cluster_resources()
    used = _used_vmids(resources)
    return {
        "vlans": list(s.vlan_gateways.keys()),
        "vlan_gateways": s.vlan_gateways,
        "default_ssh_keys": s.default_ssh_keys,
        "next_vmid": _next_free_vmid(used),
        "storages": [s.clone_storage],
        # so the form can say "taken" / "protected" before anyone clicks create
        "used_vmids": sorted(used),
        "protected_vmids": sorted(s.protected_vmids),
        "min_vmid": PVE_MIN_VMID,
        "max_vmid": PVE_MAX_VMID,
    }


def _used_vmids(resources: list[dict]) -> set[int]:
    return {r["vmid"] for r in resources if r.get("vmid")}


def _next_free_vmid(used: set[int]) -> int:
    vmid = MIN_VMID
    while vmid in used:
        vmid += 1
    return vmid


class CreateVm(BaseModel):
    name: str
    template_vmid: int
    # None = let the panel take the next free VMID. A typed one must be free, and
    # must not be protected — cloning onto a VMID would overwrite that guest.
    vmid: int | None = Field(default=None, ge=PVE_MIN_VMID, le=PVE_MAX_VMID)
    cores: int = Field(ge=1, le=32)
    memory_mb: int = Field(ge=256, le=131072)
    disk_gb: int = Field(ge=1, le=2048)
    vlan: str
    ip_cidr: str  # e.g. 10.0.20.50/24
    gateway: str = ""  # empty allowed (gateway-less VLANs, e.g. storage)
    ssh_keys: str = ""
    start: bool = True


async def _apply_cloudinit_and_size(
    pve: PveClient, vmid: int, body: CreateVm, mac: str | None = None
) -> list[str]:
    """Shared post-clone config for create + reinstall. Returns UPIDs of async steps."""
    upids: list[str] = []
    config: dict = {
        "cores": body.cores,
        "memory": body.memory_mb,
        "net0": _net0(body.vlan, mac),
        "agent": "enabled=1",
        "onboot": 1,
        "ciuser": settings().admin_user,
    }
    if body.ip_cidr:
        config["ipconfig0"] = f"ip={body.ip_cidr}" + (
            f",gw={body.gateway}" if body.gateway else ""
        )
    keys = body.ssh_keys.strip() or settings().default_ssh_keys.strip()
    if keys:
        config["sshkeys"] = urllib.parse.quote(keys, safe="")
    await pve.put(f"/nodes/{pve.node}/qemu/{vmid}/config", **config)

    tpl_config = await pve.vm_config(body.template_vmid)
    tpl_disk = tpl_config.get("scsi0", "")
    size_m = re.search(r"size=(\d+)([MGT]?)", tpl_disk)
    tpl_gb = 0
    if size_m:
        n, unit = int(size_m.group(1)), size_m.group(2)
        tpl_gb = n * 1024 if unit == "T" else n if unit == "G" else n // 1024
    if body.disk_gb > tpl_gb:
        await pve.put(
            f"/nodes/{pve.node}/qemu/{vmid}/resize",
            disk="scsi0", size=f"{body.disk_gb}G",
        )
    if body.start:
        upids.append(await pve.post(f"/nodes/{pve.node}/qemu/{vmid}/status/start"))
    return upids


def _validate_create(body: CreateVm) -> None:
    s = settings()
    if not NAME_RE.match(body.name):
        raise HTTPException(400, "Name must be a valid lowercase hostname (a-z, 0-9, hyphens)")
    if body.vlan not in s.vlan_gateways:
        raise HTTPException(400, f"VLAN must be one of {sorted(s.vlan_gateways)}")
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$", body.ip_cidr):
        raise HTTPException(400, "ip_cidr must look like 10.0.20.50/24")
    if body.gateway and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", body.gateway):
        raise HTTPException(400, "gateway must be an IPv4 address or empty")


@router.post("/api/vms", status_code=201)
async def create_vm(
    body: CreateVm,
    request: Request,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(rate_limited("vm.provision", 10, 3600.0)),
    _=Depends(require_csrf),
):
    user = await get_current_user(username, db)
    if not is_admin(user):
        await journal.record(db, request, username, journal.VM_PROVISION,
                             body.name, "refused: not an admin", ok=False)
        raise HTTPException(403, "Only admins can create VMs")
    _validate_create(body)
    resources = await pve.cluster_resources()
    if any(r.get("name") == body.name for r in resources):
        raise HTTPException(409, f"A guest named '{body.name}' already exists")
    template = next(
        (r for r in resources if r.get("vmid") == body.template_vmid and r.get("template") == 1),
        None,
    )
    if not template:
        raise HTTPException(400, f"VMID {body.template_vmid} is not a template")

    used = _used_vmids(resources)
    if body.vmid is None:
        new_vmid = _next_free_vmid(used)
    else:
        new_vmid = body.vmid
        try:
            # a clone onto a live VMID would take that guest's place — never a
            # protected one, and never one that already exists
            guard_protected(new_vmid, "provision")
        except HTTPException as e:
            await journal.record(db, request, username, journal.VM_PROVISION, new_vmid,
                                 f"refused: {e.detail}", ok=False)
            raise
        if new_vmid in used:
            raise HTTPException(409, f"VMID {new_vmid} is already in use")

    upids: list[str] = []
    clone_upid = await pve.post(
        f"/nodes/{pve.node}/qemu/{body.template_vmid}/clone",
        newid=new_vmid, name=body.name, full=1, storage=settings().clone_storage,
    )
    upids.append(clone_upid)
    await pve.wait_task(clone_upid, timeout=600)
    upids.extend(await _apply_cloudinit_and_size(pve, new_vmid, body))
    await journal.record(db, request, username, journal.VM_PROVISION, new_vmid, body.name)
    return {"vmid": new_vmid, "upids": upids}


class Reinstall(BaseModel):
    template_vmid: int
    confirm_name: str


@router.post("/api/vms/{vmid}/reinstall")
async def reinstall_vm(
    vmid: int,
    body: Reinstall,
    request: Request,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(rate_limited("vm.reinstall", 5, 3600.0)),
    _=Depends(require_csrf),
):
    user = await get_current_user(username, db)
    try:
        if not is_admin(user):
            raise HTTPException(403, "Only admins can reinstall VMs")
        guard_protected(vmid, "reinstall")
    except HTTPException as e:
        await journal.record(db, request, username, journal.VM_REINSTALL, vmid,
                             f"refused: {e.detail}", ok=False)
        raise
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    if pve.guest_kind(resource) != "qemu":
        raise HTTPException(400, "Reinstall only supports QEMU VMs")
    name = resource.get("name") or ""
    if body.confirm_name != name:
        raise HTTPException(400, f"confirm_name does not match VM name '{name}'")

    # preserve identity: MAC, VLAN, ipconfig, sizing
    old = await pve.vm_config(vmid)
    net0_parts = dict(
        p.split("=", 1) for p in old.get("net0", "").split(",") if "=" in p
    )
    mac = next(
        (v for k, v in net0_parts.items() if k in ("virtio", "e1000", "vmxnet3", "rtl8139")),
        None,
    )
    vlan = net0_parts.get("tag", "20")
    ipconfig0 = old.get("ipconfig0", "")
    ip_m = re.search(r"ip=([^,]+)", ipconfig0)
    gw_m = re.search(r"gw=([^,]+)", ipconfig0)
    disk_m = re.search(r"size=(\d+)G", old.get("scsi0", ""))

    upids: list[str] = []
    if resource.get("status") == "running":
        upid = await pve.post(f"/nodes/{pve.node}/qemu/{vmid}/status/stop")
        upids.append(upid)
        await pve.wait_task(upid)
    upid = await pve.delete(
        f"/nodes/{pve.node}/qemu/{vmid}",
        purge=1, **{"destroy-unreferenced-disks": 1},
    )
    upids.append(upid)
    await pve.wait_task(upid)

    clone_upid = await pve.post(
        f"/nodes/{pve.node}/qemu/{body.template_vmid}/clone",
        newid=vmid, name=name, full=1, storage=settings().clone_storage,
    )
    upids.append(clone_upid)
    await pve.wait_task(clone_upid, timeout=600)

    recreate = CreateVm(
        name=name,
        template_vmid=body.template_vmid,
        cores=int(old.get("cores") or 1),
        memory_mb=int(old.get("memory") or 1024),
        disk_gb=int(disk_m.group(1)) if disk_m else 1,
        vlan=vlan,
        ip_cidr=ip_m.group(1) if ip_m else "",
        gateway=gw_m.group(1) if gw_m else "",
        start=True,
    )
    if not ip_m:
        recreate.start = False  # no static IP recorded — configure before boot
    upids.extend(await _apply_cloudinit_and_size(pve, vmid, recreate, mac=mac))
    await journal.record(db, request, username, journal.VM_REINSTALL, vmid,
                         f"template {body.template_vmid}")
    return {"vmid": vmid, "upids": upids}


@router.delete("/api/vms/{vmid}")
async def destroy_vm(
    vmid: int,
    request: Request,
    confirm_name: str = Body(embed=True),
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(rate_limited("vm.destroy", 5, 3600.0)),
    _=Depends(require_csrf),
):
    user = await get_current_user(username, db)
    try:
        if not is_admin(user):
            raise HTTPException(403, "Only admins can destroy VMs")
        guard_protected(vmid, "destroy")
    except HTTPException as e:
        await journal.record(db, request, username, journal.VM_DESTROY, vmid,
                             f"refused: {e.detail}", ok=False)
        raise
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    name = resource.get("name") or ""
    if confirm_name != name:
        raise HTTPException(400, f"confirm_name does not match VM name '{name}'")
    kind = pve.guest_kind(resource)

    upids: list[str] = []
    if resource.get("status") == "running":
        upid = await pve.post(f"/nodes/{pve.node}/{kind}/{vmid}/status/stop")
        upids.append(upid)
        await pve.wait_task(upid)
    upid = await pve.delete(
        f"/nodes/{pve.node}/{kind}/{vmid}",
        purge=1, **{"destroy-unreferenced-disks": 1},
    )
    upids.append(upid)
    await journal.record(db, request, username, journal.VM_DESTROY, vmid, f"name={name}")
    return {"upids": upids}
