"""End-to-end test of the noVNC console WebSocket proxy.

The panel's console handler is the one backend flow the rest of the suite never
exercises: it makes an *outbound* ``websockets.connect`` to PVE and pumps bytes
both ways. A TestClient (in-process, fake ASGI transport) can't model that, so
this module stands up two real uvicorn servers on ephemeral ports:

    browser ──ws──▶ panel (hlidskjalf) ──ws──▶ mock PVE vncwebsocket (echo)

The mock's ``/api2/json/.../vncwebsocket`` echoes every binary frame, so a byte
that returns to the browser has travelled the whole path in both directions —
proving the pump is bidirectional. These servers are fully independent of the
session-scoped fixtures in conftest (which point the in-process app at a
*different* mock); nothing here touches that shared state.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
import websockets
from argon2 import PasswordHasher

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "dev"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "console-test-password"
COOKIE_NAME = "hlidskjalf_session"  # matches hlidskjalf.auth.COOKIE_NAME


def _free_port() -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_uvicorn(app_path: str, port: int, cwd: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", app_path,
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(cwd), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def _wait_ready(url: str, proc: subprocess.Popen, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"server exited early: {proc.stderr.read().decode(errors='replace')}"
            )
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            proc.terminate()
            raise RuntimeError(f"server did not become ready within {timeout}s: {url}")
        time.sleep(0.1)


@pytest.fixture(scope="session")
def panel():
    """Live mock-PVE + live panel; yields (base_http_url, panel_port)."""
    mock_port = _free_port()
    panel_port = _free_port()
    state_dir = tempfile.mkdtemp(prefix="hlidskjalf-console-state-")

    env = os.environ.copy()
    env.update(
        {
            "HLIDSKJALF_PVE_SCHEME": "http",
            "HLIDSKJALF_PVE_HOST": "127.0.0.1",
            "HLIDSKJALF_PVE_PORT": str(mock_port),
            "HLIDSKJALF_PVE_NODE": "pve",
            "HLIDSKJALF_PVE_TOKEN_ID": "hlidskjalf@pve!panel",
            "HLIDSKJALF_PVE_TOKEN_SECRET": "mock-secret",
            "HLIDSKJALF_ADMIN_USER": ADMIN_USER,
            "HLIDSKJALF_ADMIN_PASSWORD_HASH": PasswordHasher().hash(ADMIN_PASSWORD),
            "HLIDSKJALF_SESSION_SECRET": "0123456789abcdef" * 4,
            "HLIDSKJALF_STATE_DIR": state_dir,
            "HLIDSKJALF_STATIC_DIR": "",
        }
    )

    mock = _spawn_uvicorn("mock_pve:app", mock_port, DEV_DIR, env)
    procs = [mock]
    try:
        _wait_ready(f"http://127.0.0.1:{mock_port}/api2/json/cluster/resources", mock)
        panel_proc = _spawn_uvicorn("hlidskjalf.main:app", panel_port, REPO_ROOT, env)
        procs.append(panel_proc)
        _wait_ready(f"http://127.0.0.1:{panel_port}/api/health", panel_proc)
        yield f"http://127.0.0.1:{panel_port}", panel_port
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(state_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def cookie(panel):
    """Log in exactly once for the whole module.

    /api/login is rate-limited (5/min) and the panel is a separate process, so
    the in-process ``auth._login_attempts`` reset used elsewhere can't help
    here. The signed session cookie is valid for hours, so one login is reused
    across every test; the per-test console ticket GET is not rate-limited.
    """
    base_url, _ = panel
    r = httpx.post(
        f"{base_url}/api/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    value = r.cookies.get(COOKIE_NAME)
    assert value, "login did not set a session cookie"
    return value


async def _ticket(base_url: str, cookie: str, vmid: int = 105) -> dict:
    """Request a console ticket for vmid with an already-authenticated cookie."""
    async with httpx.AsyncClient(base_url=base_url, cookies={COOKIE_NAME: cookie}) as c:
        r = await c.get(f"/api/vms/{vmid}/console")
        assert r.status_code == 200, r.text
        return r.json()


def _cookie_header(value: str | None) -> dict:
    return {"Cookie": f"{COOKIE_NAME}={value}"} if value is not None else {}


def _key_of(ws_path: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlparse(ws_path).query)["key"][0]


async def _expect_close(ws_url: str, cookie: str | None) -> int:
    """Connect, expect the server to close the (accepted) socket; return code."""
    ws = await websockets.connect(
        ws_url, additional_headers=_cookie_header(cookie), subprotocols=["binary"]
    )
    try:
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(ws.recv(), timeout=5)
        return ws.close_code
    finally:
        await ws.close()


# --- happy path -------------------------------------------------------------


async def test_console_ticket_shape(panel, cookie):
    base_url, _ = panel
    body = await _ticket(base_url, cookie, 105)
    assert body["kind"] == "qemu"
    assert body["password"] == "MOCK-TICKET-105"  # the vncproxy ticket noVNC needs
    assert body["ws_path"].startswith("/ws/console/105?key=")


async def test_console_echo_is_bidirectional(panel, cookie):
    base_url, port = panel
    body = await _ticket(base_url, cookie, 105)
    ws_url = f"ws://127.0.0.1:{port}{body['ws_path']}"

    async with websockets.connect(
        ws_url, additional_headers=_cookie_header(cookie), subprotocols=["binary"]
    ) as ws:
        assert ws.subprotocol == "binary"
        # send (client -> panel -> mock) and receive the echo (mock -> panel ->
        # client): a full round-trip proves both pump directions move bytes.
        first = b"RFB 003.008\n\x00\x01\x02\x03\xff"
        await ws.send(first)
        assert await asyncio.wait_for(ws.recv(), timeout=5) == first
        # a second frame proves the pump keeps running, not one-shot
        second = b"\xde\xad\xbe\xef" * 32
        await ws.send(second)
        assert await asyncio.wait_for(ws.recv(), timeout=5) == second


async def test_console_ws_accepts_a_client_that_offers_no_subprotocol(panel, cookie):
    """noVNC is that client, and the panel used to hang up on it.

    RFB 6455 §4.1: a client MUST fail the connection if the server selects a
    subprotocol it did not offer. noVNC >= 1.5 offers NONE (`wsProtocols: []`),
    while the panel answered "binary" unconditionally — so every VM console died
    on arrival ("connection lost unexpectedly", black screen) while the xterm.js
    terminal, which asks for "binary" explicitly, worked fine. Negotiate; never
    assert.
    """
    base_url, port = panel
    body = await _ticket(base_url, cookie, 105)  # qemu — the noVNC path
    ws_url = f"ws://127.0.0.1:{port}{body['ws_path']}"

    async with websockets.connect(  # note: NO subprotocols= argument
        ws_url, additional_headers=_cookie_header(cookie)
    ) as ws:
        assert ws.subprotocol is None  # the server must not invent one
        await ws.send(b"RFB 003.008\n")  # and the pump must still work
        assert await asyncio.wait_for(ws.recv(), timeout=5) == b"RFB 003.008\n"


# --- containers: a terminal, not a framebuffer ------------------------------
# Validated against real PVE 9.2.3 (2026-07-13): an LXC guest's vncproxy
# completes the RFB handshake and then hangs forever at ClientInit — with the
# panel out of the path entirely. Proxmox drives containers through termproxy,
# which yields a live shell. These are the tests that would have caught the panel
# serving a dead VNC console for every container.


async def test_lxc_console_uses_termproxy_and_withholds_the_ticket(panel, cookie):
    base_url, _ = panel
    body = await _ticket(base_url, cookie, 130)  # the mock's LXC guest
    assert body["kind"] == "lxc"
    # The panel authenticates termproxy upstream itself, so a container's ticket
    # must NEVER reach the browser (unlike noVNC's RFB password, which must).
    assert body["password"] == ""
    assert body["ws_path"].startswith("/ws/console/130?key=")


async def test_lxc_console_authenticates_upstream_then_pumps(panel, cookie):
    """The mock's lxc socket demands termproxy's "<user>:<ticket>" line and
    answers OK, exactly as real PVE does. Had the panel skipped that handshake —
    or sent it as a binary VNC frame — the socket would close instead of echo."""
    base_url, port = panel
    body = await _ticket(base_url, cookie, 130)
    ws_url = f"ws://127.0.0.1:{port}{body['ws_path']}"

    async with websockets.connect(
        ws_url, additional_headers=_cookie_header(cookie), subprotocols=["binary"]
    ) as ws:
        # The panel already swallowed the upstream "OK"; the first thing we see
        # is our own echo. termproxy framing is TEXT, not binary.
        await ws.send("0:5:hello")
        assert await asyncio.wait_for(ws.recv(), timeout=5) == "0:5:hello"
        await ws.send("1:80:24:")  # resize frames keep flowing too
        assert await asyncio.wait_for(ws.recv(), timeout=5) == "1:80:24:"


# --- negative paths (auth + one-time key) -----------------------------------


async def test_console_ws_missing_session_closes_4401(panel, cookie):
    base_url, port = panel
    # obtain a *valid* key so the session cookie is the only thing missing
    body = await _ticket(base_url, cookie, 105)
    ws_url = f"ws://127.0.0.1:{port}{body['ws_path']}"
    assert await _expect_close(ws_url, cookie=None) == 4401


async def test_console_ws_bad_or_absent_key_closes_4403(panel, cookie):
    base_url, port = panel  # valid session cookie, bad/missing key
    bad = f"ws://127.0.0.1:{port}/ws/console/105?key=not-a-real-key"
    assert await _expect_close(bad, cookie=cookie) == 4403
    absent = f"ws://127.0.0.1:{port}/ws/console/105"
    assert await _expect_close(absent, cookie=cookie) == 4403


async def test_console_ws_key_is_one_time(panel, cookie):
    base_url, port = panel
    body = await _ticket(base_url, cookie, 105)
    ws_url = f"ws://127.0.0.1:{port}{body['ws_path']}"

    # first use redeems the key successfully
    async with websockets.connect(
        ws_url, additional_headers=_cookie_header(cookie), subprotocols=["binary"]
    ) as ws:
        await ws.send(b"once")
        assert await asyncio.wait_for(ws.recv(), timeout=5) == b"once"

    # second use of the same key is rejected (key was popped on first use)
    assert await _expect_close(ws_url, cookie=cookie) == 4403


async def test_console_ws_vmid_mismatch_closes_4403(panel, cookie):
    base_url, port = panel
    body = await _ticket(base_url, cookie, 105)  # key bound to 105
    key = _key_of(body["ws_path"])
    other = f"ws://127.0.0.1:{port}/ws/console/115?key={key}"
    assert await _expect_close(other, cookie=cookie) == 4403
