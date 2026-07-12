"""Switch port visualization API (Arista 7050TX etc.).

Graceful degradation:
- Always returns a response even if switch unreachable / misconfigured.
- Includes optional "error" string for UI (last known ports still merged).
- Notes always loaded from local DB.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session
from ..db import Db
from ..deps import get_db, get_switch
from ..switch import AristaClient, PortInfo

router = APIRouter()


@router.get("/api/switch/ports")
async def list_ports(
    client: AristaClient = Depends(get_switch),
    db: Db = Depends(get_db),
    _=Depends(require_session),
) -> dict:
    """Return { ports: [...], error?: string }.

    ports always present (may be empty or last-known on failure).
    error present only on degradation (for user message, without crashing).
    """
    error: str | None = None
    try:
        raw_ports: list[PortInfo] = await client.get_ports()
    except Exception as exc:  # belt-and-suspenders (client already catches most)
        raw_ports = []
        error = f"switch error: {str(exc)[:180]}"
    notes = await db.get_port_notes()

    result = []
    for p in raw_ports:
        note = notes.get(p.name, "")
        result.append(
            {
                "name": p.name,
                "status": p.status,
                "speed": p.speed,
                "duplex": p.duplex,
                "vlan": p.vlan,
                "description": p.description,
                "note": note,
                "inputRate": p.input_rate,
                "outputRate": p.output_rate,
                "active": p.active,
                "lldpNeighbor": p.lldp_neighbor,
            }
        )
    resp: dict[str, Any] = {"ports": result}
    if error:
        resp["error"] = error
    return resp


@router.post("/api/switch/ports/{name}/note")
async def set_port_note(
    name: str,
    payload: dict,
    db: Db = Depends(get_db),
    _=Depends(require_session),
):
    note = payload.get("note", "")
    if not isinstance(note, str):
        raise HTTPException(400, "note must be a string")

    await db.set_port_note(name, note.strip())
    return {"ok": True, "name": name, "note": note.strip()}
