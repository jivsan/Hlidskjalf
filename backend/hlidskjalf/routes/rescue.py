"""Rescue mode: boot a VM from the SystemRescue ISO and back.

Enter: stash the original boot order + the ide slot we borrow, attach the ISO,
boot from it, power-cycle. Exit: restore both, power-cycle. The stash lives in
the panel's sqlite so it survives restarts; the UI shows an amber RESCUE MODE
banner for any vmid present in the stash.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_csrf, require_session
from ..db import Db
from ..deps import get_db, get_pve, guard_protected, settings
from ..pve import PveClient
from .vms import _ensure_vm_access

router = APIRouter()

UNSET = "\x00unset"  # marker: slot/boot key was absent from config


def _pick_slot(config: dict) -> str:
    """Free ide slot for the rescue CD — prefer ide0; cloud-init usually sits on ide2."""
    for slot in ("ide0", "ide1", "ide3", "ide2"):
        if slot not in config:
            return slot
    raise HTTPException(409, "No free ide slot for the rescue ISO")


async def _power_cycle(pve: PveClient, vmid: int, running: bool) -> list[str]:
    """stop+start rather than reboot — reboot needs a cooperative guest, and a
    guest needing rescue usually isn't."""
    upids = []
    if running:
        upid = await pve.post(f"/nodes/{pve.node}/qemu/{vmid}/status/stop")
        upids.append(upid)
        await pve.wait_task(upid)
    upid = await pve.post(f"/nodes/{pve.node}/qemu/{vmid}/status/start")
    upids.append(upid)
    return upids


@router.post("/api/vms/{vmid}/rescue")
async def enter_rescue(
    vmid: int,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
    _=Depends(require_csrf),
):
    # Authorize: owner or admin only. Rescue power-cycles the VM into an ISO, so
    # an unscoped endpoint let any logged-in user reboot any tenant's machine.
    await _ensure_vm_access(username, vmid, db, pve)
    # Refuse protected VMIDs for EVERYONE (admins included): rescuing protected
    # infrastructure — heimdall hosts this very panel, PBS, etc. — is catastrophic.
    guard_protected(vmid, "rescue")
    iso = settings().rescue_iso
    if not iso:
        raise HTTPException(500, "HLIDSKJALF_RESCUE_ISO not configured")
    if await db.rescue_get(vmid):
        raise HTTPException(409, f"VMID {vmid} is already in rescue mode")
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    if pve.guest_kind(resource) != "qemu":
        raise HTTPException(400, "Rescue mode only supports QEMU VMs")

    config = await pve.vm_config(vmid)
    slot = _pick_slot(config)
    await db.rescue_set(
        vmid,
        boot=config.get("boot", UNSET),
        slot=slot,
        slot_prev=config.get(slot, UNSET),
    )
    await pve.put(
        f"/nodes/{pve.node}/qemu/{vmid}/config",
        **{slot: f"{iso},media=cdrom", "boot": f"order={slot}"},
    )
    upids = await _power_cycle(pve, vmid, resource.get("status") == "running")
    return {"rescue": True, "slot": slot, "upids": upids}


@router.delete("/api/vms/{vmid}/rescue")
async def exit_rescue(
    vmid: int,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
    _=Depends(require_csrf),
):
    # Authorize exit too (owner or admin). No guard_protected here: a protected
    # VM can never have entered rescue, and you never want to block recovery.
    await _ensure_vm_access(username, vmid, db, pve)
    stash = await db.rescue_get(vmid)
    if not stash:
        raise HTTPException(409, f"VMID {vmid} is not in rescue mode")
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")

    slot = stash["slot"]
    config: dict = {}
    delete: list[str] = []
    if stash["slot_prev"] == UNSET:
        delete.append(slot)
    else:
        config[slot] = stash["slot_prev"]
    if stash["boot"] == UNSET:
        delete.append("boot")
    else:
        config["boot"] = stash["boot"]
    if delete:
        config["delete"] = ",".join(delete)
    await pve.put(f"/nodes/{pve.node}/qemu/{vmid}/config", **config)
    await db.rescue_clear(vmid)
    upids = await _power_cycle(pve, vmid, resource.get("status") == "running")
    return {"rescue": False, "upids": upids}
