"""noVNC console: vncproxy ticket + bidirectional WebSocket byte pump.

Flow: the SPA calls GET /api/vms/{vmid}/console; the panel asks PVE for a
vncproxy ticket, stashes {port, ticket} under a one-time key, and returns the
local WS path plus the ticket (noVNC needs it as the RFB password). The SPA
then opens /ws/console/{vmid}?key=...; the handler checks the session cookie,
redeems the key, dials hella's vncwebsocket with the API token header, and
pumps bytes both ways.
"""

import asyncio
import logging
import secrets
import time
import urllib.parse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
import websockets

from ..auth import require_session, session_from_request
from ..deps import get_pve
from ..pve import PveClient, PveError

log = logging.getLogger("hlidskjalf.console")
router = APIRouter()

# one-time key -> (vmid, port, ticket, created_at); keys expire fast, the PVE
# vncticket itself is only valid for a short window anyway
_pending: dict[str, tuple[int, str, str, float]] = {}
KEY_TTL = 60.0


def _reap() -> None:
    now = time.monotonic()
    for k in [k for k, v in _pending.items() if now - v[3] > KEY_TTL]:
        _pending.pop(k, None)


@router.get("/api/vms/{vmid}/console")
async def console_ticket(
    vmid: int, pve: PveClient = Depends(get_pve), _=Depends(require_session)
):
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    kind = pve.guest_kind(resource)
    if resource.get("status") != "running":
        raise HTTPException(409, "Guest is not running — start it first")
    data = await pve.post(f"/nodes/{pve.node}/{kind}/{vmid}/vncproxy", websocket=1)
    _reap()
    key = secrets.token_urlsafe(24)
    _pending[key] = (vmid, str(data["port"]), data["ticket"], time.monotonic())
    return {
        "ws_path": f"/ws/console/{vmid}?key={key}",
        "password": data["ticket"],
        "kind": kind,
    }


@router.websocket("/ws/console/{vmid}")
async def console_ws(websocket: WebSocket, vmid: int, key: str = ""):
    # Accept the handshake *before* the auth/key checks so a rejection can send
    # a real WebSocket close code (4401/4403) to the browser. Closing before
    # accept makes uvicorn reject the handshake with a bare HTTP 403 and the
    # code never reaches the client (noVNC would only see a generic 1006).
    # noVNC always offers the "binary" subprotocol.
    await websocket.accept(subprotocol="binary")
    try:
        session_from_request(websocket)  # cookie check; raises HTTPException
    except HTTPException:
        await websocket.close(code=4401)
        return
    entry = _pending.pop(key, None)
    if not entry or entry[0] != vmid or time.monotonic() - entry[3] > KEY_TTL:
        await websocket.close(code=4403)
        return
    _, port, ticket, _ = entry

    pve: PveClient = websocket.app.state.pve
    settings = pve.settings
    resource = await pve.find_resource(vmid)
    kind = pve.guest_kind(resource) if resource else "qemu"
    scheme = "wss" if settings.pve_scheme == "https" else "ws"
    upstream_url = (
        f"{scheme}://{settings.pve_host}:{settings.pve_port}"
        f"/api2/json/nodes/{pve.node}/{kind}/{vmid}/vncwebsocket"
        f"?port={port}&vncticket={urllib.parse.quote(ticket, safe='')}"
    )
    headers = {"Authorization": f"PVEAPIToken={settings.pve_token_id}={settings.pve_token_secret}"}

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=headers,
            ssl=pve.ssl_context if scheme == "wss" else None,
            subprotocols=["binary"],
            max_size=None,
        ) as upstream:

            async def pump_up():
                while True:
                    data = await websocket.receive_bytes()
                    await upstream.send(data)

            async def pump_down():
                async for msg in upstream:
                    if isinstance(msg, str):
                        msg = msg.encode()
                    await websocket.send_bytes(msg)

            tasks = [asyncio.create_task(pump_up()), asyncio.create_task(pump_down())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except (OSError, websockets.WebSocketException, PveError) as e:
        log.warning("console proxy for vmid %d failed: %s", vmid, e)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
