"""Admin-editable provisioning settings.

Verified against a real PVE 9.2.3 host (2026-07-13): with the env-var-only
defaults, provisioning is unusable out of the box — `vlan_gateways` defaults to
{} so no create ever validates, `clone_storage` defaults to a storage the host
may not have, and the bridge used to be hardcoded. This route lets an admin fix
all three from the panel, persisted through the same seal/apply_stored path the
setup wizard uses (config table, encrypted-at-rest where applicable).

Precedence is the house rule: **environment always wins.** A key supplied via
its HLIDSKJALF_* env var is reported as locked and a PUT refusing to change it
— mirroring config.apply_stored, which would ignore the stored value anyway.
The allowlist is ADMIN_WRITABLE, deliberately separate from SETUP_WRITABLE:
these endpoints require an admin session, setup's are unauthenticated.
"""

import json
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import journal
from ..auth import require_admin_user, require_csrf
from ..config import ADMIN_WRITABLE, apply_stored, get_settings, seal
from ..db import Db
from ..deps import get_db

log = logging.getLogger("hlidskjalf.settings")
router = APIRouter()

# API name -> Settings field name. "bridge" reads better on the wire than the
# internal pve_bridge (which is prefixed to sit with the other PVE knobs).
FIELD_FOR = {
    "vlan_gateways": "vlan_gateways",
    "clone_storage": "clone_storage",
    "bridge": "pve_bridge",
}
assert set(FIELD_FOR.values()) == set(ADMIN_WRITABLE)

IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")  # same shape provision.py accepts


def _env_locked(field: str) -> bool:
    """True when the operator supplies `field` via the environment.

    Mirrors config.apply_stored's precedence: env always wins, but an env var
    defined yet EMPTY is not a configuration choice and does not lock the key.
    """
    return bool(os.environ.get(f"HLIDSKJALF_{field.upper()}", "").strip())


def _locked_keys() -> list[str]:
    return [k for k, f in FIELD_FOR.items() if _env_locked(f)]


async def _live_options(request: Request) -> tuple[dict, str | None]:
    """Storages and bridges the node actually has. Never raises.

    Returns (options, warning). Empty lists + a warning when PVE is not
    reachable — the operator may be here precisely to repair a broken config,
    so the page must still load.
    """
    pve = getattr(request.app.state, "pve", None)
    if pve is None:
        return {"storages": [], "bridges": []}, "Proxmox is not configured yet"
    try:
        storages = await pve.get(f"/nodes/{pve.node}/storage") or []
        networks = await pve.get(f"/nodes/{pve.node}/network") or []
    except Exception as e:  # PveError, timeouts — degrade, don't 500
        log.warning("could not fetch provision options from PVE: %s", e)
        return {"storages": [], "bridges": []}, f"could not query Proxmox: {e}"
    return {
        "storages": sorted(
            s["storage"]
            for s in storages
            if s.get("storage") and "images" in (s.get("content") or "")
        ),
        "bridges": sorted(
            n["iface"] for n in networks if n.get("type") == "bridge" and n.get("iface")
        ),
    }, None


def _view(options: dict, warning: str | None) -> dict:
    s = get_settings()
    return {
        "vlan_gateways": s.vlan_gateways,
        "clone_storage": s.clone_storage,
        "bridge": s.pve_bridge,
        "env_locked": _locked_keys(),
        "options": options,
        "warning": warning,
    }


@router.get("/api/settings/provision")
async def get_provision_settings(
    request: Request,
    _admin: dict = Depends(require_admin_user),
):
    options, warning = await _live_options(request)
    return _view(options, warning)


class ProvisionSettingsBody(BaseModel):
    vlan_gateways: dict[str, str]
    clone_storage: str
    bridge: str


def _validate(body: ProvisionSettingsBody, options: dict, live: bool) -> None:
    for tag, gateway in body.vlan_gateways.items():
        if not tag.isdigit() or not 1 <= int(tag) <= 4094:
            raise HTTPException(400, f"VLAN tag '{tag}' must be an integer 1..4094")
        if gateway and not IPV4_RE.match(gateway):
            raise HTTPException(
                400, f"gateway for VLAN {tag} must be an IPv4 address or empty"
            )
    if not body.clone_storage.strip():
        raise HTTPException(400, "clone_storage must not be empty")
    if not body.bridge.strip():
        raise HTTPException(400, "bridge must not be empty")
    # Validate against what the node actually has — but only when we could ask.
    # A failed lookup must not brick this page: the operator may be correcting
    # a broken storage/bridge right now.
    if live:
        if options["storages"] and body.clone_storage not in options["storages"]:
            raise HTTPException(
                400,
                f"storage '{body.clone_storage}' does not exist on the node "
                f"(image-capable storages: {', '.join(options['storages'])})",
            )
        if options["bridges"] and body.bridge not in options["bridges"]:
            raise HTTPException(
                400,
                f"bridge '{body.bridge}' does not exist on the node "
                f"(bridges: {', '.join(options['bridges'])})",
            )


@router.put("/api/settings/provision")
async def put_provision_settings(
    body: ProvisionSettingsBody,
    request: Request,
    db: Db = Depends(get_db),
    admin: dict = Depends(require_admin_user),
    _csrf=Depends(require_csrf),
):
    settings = get_settings()
    options, warning = await _live_options(request)
    _validate(body, options, live=warning is None)

    # Refuse to change anything the environment owns — the change would look
    # accepted and then silently revert on the next restart (env always wins).
    submitted = {
        "vlan_gateways": body.vlan_gateways,
        "clone_storage": body.clone_storage.strip(),
        "bridge": body.bridge.strip(),
    }
    current = {
        "vlan_gateways": settings.vlan_gateways,
        "clone_storage": settings.clone_storage,
        "bridge": settings.pve_bridge,
    }
    for key in _locked_keys():
        if submitted[key] != current[key]:
            raise HTTPException(
                400,
                f"'{key}' is set by the environment "
                f"(HLIDSKJALF_{FIELD_FOR[key].upper()}) — unset the variable to "
                "manage it here",
            )

    # Persist through the same path the wizard uses: string values into the
    # config table (sealed — a no-op for these non-secret keys), then overlay
    # onto the live Settings object so it takes effect without a restart.
    stored = {
        FIELD_FOR[key]: value if isinstance(value, str) else json.dumps(value)
        for key, value in submitted.items()
        if key not in _locked_keys()  # never shadow-store what env owns
    }
    assert set(stored) <= ADMIN_WRITABLE  # the allowlist is the boundary
    await db.set_config(seal(stored, settings))
    apply_stored(settings, stored)

    await journal.record(
        db,
        request,
        admin["username"],
        journal.SETTINGS_UPDATE,
        "provision",
        f"vlans={sorted(submitted['vlan_gateways'])} "
        f"storage={submitted['clone_storage']} bridge={submitted['bridge']}",
    )
    return _view(options, warning)
