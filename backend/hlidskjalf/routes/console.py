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
from ..db import Db
from ..deps import get_db, get_pve
from ..pve import PveClient, PveError
from .vms import _ensure_vm_access

log = logging.getLogger("hlidskjalf.console")
router = APIRouter()

# one-time key -> (vmid, port, ticket, created_at, owner); keys expire fast, the
# PVE vncticket itself is only valid for a short window anyway. `owner` binds the
# key to the user who minted it: the key travels in a URL query string (which
# lands in proxy/access logs and browser history), so a valid key alone must not
# be enough for a *different* authenticated user to redeem someone's console.
_pending: dict[str, tuple[int, str, str, float, str]] = {}
KEY_TTL = 60.0


def _reap() -> None:
    now = time.monotonic()
    for k in [k for k, v in _pending.items() if now - v[3] > KEY_TTL]:
        _pending.pop(k, None)


@router.get("/api/vms/{vmid}/console")
async def console_ticket(
    vmid: int,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    username: str = Depends(require_session),
):
    # Per-user VM scoping: admins pass; a regular user is 403'd unless the
    # requested vmid is the one VM assigned to them. Without this a tenant could
    # mint a VNC ticket for another tenant's machine (IDOR).
    await _ensure_vm_access(username, vmid, db, pve)
    resource = await pve.find_resource(vmid)
    if not resource:
        raise HTTPException(404, f"No guest with VMID {vmid}")
    kind = pve.guest_kind(resource)
    if resource.get("status") != "running":
        raise HTTPException(409, "Guest is not running — start it first")
    data = await pve.post(f"/nodes/{pve.node}/{kind}/{vmid}/vncproxy", websocket=1)
    _reap()
    key = secrets.token_urlsafe(24)
    _pending[key] = (vmid, str(data["port"]), data["ticket"], time.monotonic(), username)
    return {
        "ws_path": f"/ws/console/{vmid}?key={key}",
        "password": data["ticket"],
        "kind": kind,
    }


@router.websocket("/ws/console/{vmid}")
async def console_ws(websocket: WebSocket, vmid: int, key: str = ""):
    # The one-time `key` is only ever minted by console_ticket above, which is
    # ownership-scoped via _ensure_vm_access — so a key already encodes "this
    # user may reach this vmid". We still re-check the session here AND require
    # that the redeeming session is the same user who minted the key: the key is
    # carried in a URL query string, so leaking it (logs, history, a shared
    # screen) must not let another logged-in tenant open the console.
    # Accept the handshake *before* the auth/key checks so a rejection can send
    # a real WebSocket close code (4401/4403) to the browser. Closing before
    # accept makes uvicorn reject the handshake with a bare HTTP 403 and the
    # code never reaches the client (noVNC would only see a generic 1006).
    # noVNC always offers the "binary" subprotocol.
    await websocket.accept(subprotocol="binary")
    db: Db = websocket.app.state.db
    try:
        username = await session_from_request(websocket, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    entry = _pending.pop(key, None)
    if (
        not entry
        or entry[0] != vmid
        or time.monotonic() - entry[3] > KEY_TTL
        or not secrets.compare_digest(entry[4], username)
    ):
        await websocket.close(code=4403)
        return
    _, port, ticket, _, _ = entry

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
