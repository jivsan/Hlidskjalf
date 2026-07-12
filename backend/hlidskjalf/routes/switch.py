"""Switch port visualization API (Arista 7050TX etc.)."""

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
) -> list[dict]:
    """Return current port status with activity + merged notes."""
    raw_ports: list[PortInfo] = await client.get_ports()
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
            }
        )
    return result


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
