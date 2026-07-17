"""Guest console: a VNC framebuffer for QEMU, a terminal for LXC.

Flow: the SPA calls GET /api/vms/{vmid}/console; the panel asks PVE for a
console ticket, stashes it under a one-time key, and returns the local WS path.
The SPA then opens /ws/console/{vmid}?key=...; the handler checks the session
cookie, redeems the key, dials PVE's vncwebsocket with the API token header,
and pumps bytes both ways.

**The two guest kinds speak different protocols, and this is not cosmetic.**
Validated against a real PVE 9.2.3 host (2026-07-13): QEMU's `vncproxy` yields a
genuine RFB server (ServerInit 1280x800 came back). An LXC container's
`vncproxy` completes the RFB handshake *and then hangs forever at ClientInit* —
with the panel entirely removed from the path, straight against Proxmox. That is
why Proxmox's own UI drives containers through `termproxy` (xterm.js), not VNC.
So:

- **qemu** -> POST vncproxy -> RFB. noVNC needs the ticket as the RFB password,
  so it is handed to the browser (unavoidable; it is single-use and short-lived).
- **lxc**  -> POST termproxy -> a line-framed terminal protocol. The panel
  performs the `user:ticket\\n` authentication **upstream itself** and only starts
  pumping once PVE answers "OK", so the container's ticket never reaches the
  browser at all. The browser speaks only termproxy's frames ("0:len:data" for
  input, "1:cols:rows:" for resize, "2" for keepalive).
"""

import asyncio
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
import websockets

from ..auth import rate_limited, session_from_request
from ..db import Db
from ..deps import get_db, get_pve
from ..pve import PveClient, PveError
from .vms import _ensure_vm_access

log = logging.getLogger("hlidskjalf.console")
router = APIRouter()

@dataclass(frozen=True)
class _Pending:
    vmid: int
    kind: str  # "qemu" (RFB) or "lxc" (termproxy)
    port: str
    ticket: str
    pve_user: str  # termproxy's auth line is "<user>:<ticket>"
    created_at: float
    owner: str  # the panel user who minted this key


# one-time key -> _Pending; keys expire fast, and the PVE ticket itself is only
# valid for a short window anyway. `owner` binds the key to the user who minted
# it: the key travels in a URL query string (which lands in proxy/access logs and
# browser history), so a valid key alone must not be enough for a *different*
# authenticated user to redeem someone's console.
_pending: dict[str, _Pending] = {}
KEY_TTL = 60.0


def _reap() -> None:
    now = time.monotonic()
    for k in [k for k, v in _pending.items() if now - v.created_at > KEY_TTL]:
        _pending.pop(k, None)


@router.get("/api/vms/{vmid}/console")
async def console_ticket(
    vmid: int,
    pve: PveClient = Depends(get_pve),
    db: Db = Depends(get_db),
    # The one PVE-hitting verb the v0.3.6 hardening pass missed: every call here
    # is a vncproxy/termproxy POST against Proxmox, so an unthrottled loop here
    # is an unthrottled loop against the hypervisor. 30/hour per user — a human
    # mints a ticket per page load and per reconnect (a handful an hour at
    # most), so this only ever bites something scripted. Keyed per user like
    # every other bucket, so one tenant cannot throttle the rest.
    username: str = Depends(rate_limited("vm.console", 30, 3600.0)),
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

    if kind == "lxc":
        # Containers get a terminal, not a framebuffer — their VNC endpoint hangs
        # at ClientInit on real Proxmox (see the module docstring).
        data = await pve.post(f"/nodes/{pve.node}/lxc/{vmid}/termproxy")
    else:
        data = await pve.post(f"/nodes/{pve.node}/qemu/{vmid}/vncproxy", websocket=1)

    _reap()
    key = secrets.token_urlsafe(24)
    _pending[key] = _Pending(
        vmid=vmid,
        kind=kind,
        port=str(data["port"]),
        ticket=data["ticket"],
        pve_user=data.get("user", ""),
        created_at=time.monotonic(),
        owner=username,
    )
    return {
        "ws_path": f"/ws/console/{vmid}?key={key}",
        # noVNC needs the RFB password client-side. termproxy does NOT: the panel
        # authenticates upstream on the browser's behalf, so the container's
        # ticket never leaves this process.
        "password": data["ticket"] if kind == "qemu" else "",
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
    #
    # NEGOTIATE the subprotocol — never assert one. RFC 6455 §4.1: a client MUST
    # fail the connection if the server selects a subprotocol it did not offer.
    # We used to answer "binary" unconditionally, which browsers duly killed:
    # noVNC (>=1.5) offers NO subprotocol by default (`wsProtocols: []`), so the
    # VM console died the instant it connected — "connection lost unexpectedly",
    # black screen. The xterm.js terminal asks for "binary" explicitly and so
    # survived, which is exactly why containers worked and VMs did not.
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in offered else None)
    db: Db = websocket.app.state.db
    try:
        username = await session_from_request(websocket, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    entry = _pending.pop(key, None)
    if (
        not entry
        or entry.vmid != vmid
        or time.monotonic() - entry.created_at > KEY_TTL
        or not secrets.compare_digest(entry.owner, username)
    ):
        await websocket.close(code=4403)
        return

    pve: PveClient = websocket.app.state.pve
    settings = pve.settings
    kind = entry.kind
    scheme = "wss" if settings.pve_scheme == "https" else "ws"
    upstream_url = (
        f"{scheme}://{settings.pve_host}:{settings.pve_port}"
        f"/api2/json/nodes/{pve.node}/{kind}/{vmid}/vncwebsocket"
        f"?port={entry.port}&vncticket={urllib.parse.quote(entry.ticket, safe='')}"
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
            if kind == "lxc":
                # termproxy authenticates with a "<user>:<ticket>\n" line and
                # answers "OK". We do it here so the ticket stays server-side;
                # the browser only ever speaks the terminal framing.
                await upstream.send(f"{entry.pve_user}:{entry.ticket}\n")
                try:
                    reply = await asyncio.wait_for(upstream.recv(), timeout=10)
                except asyncio.TimeoutError:
                    log.warning("termproxy for vmid %d never answered the auth line", vmid)
                    await websocket.close(code=4502)
                    return
                if isinstance(reply, bytes):
                    reply = reply.decode("utf-8", "replace")
                if not reply.startswith("OK"):
                    log.warning("termproxy auth for vmid %d refused: %r", vmid, reply[:40])
                    await websocket.close(code=4403)
                    return

            # noVNC speaks binary frames; xterm.js/termproxy speaks text ones.
            # Forward whatever arrives, in the frame type it arrived as — coercing
            # a termproxy text frame into a binary one loses the protocol.
            async def pump_up():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(msg.get("code", 1000))
                    data = msg.get("bytes")
                    await upstream.send(data if data is not None else msg.get("text", ""))

            async def pump_down():
                async for msg in upstream:
                    if isinstance(msg, str):
                        await websocket.send_text(msg)
                    else:
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
