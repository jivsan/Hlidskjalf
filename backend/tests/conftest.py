"""Test harness for the hlidskjalf backend.

Layout:
- dev/mock_pve.py runs as a real uvicorn subprocess on a random free port
  (session-scoped) so the panel's httpx client exercises actual HTTP.
- The hlidskjalf FastAPI app runs in-process via starlette's TestClient,
  with its lifespan kept open for the whole session (accumulator, db, pve
  client wired on app.state).

Env vars MUST be set before `hlidskjalf.main` is imported: get_settings() is
lru_cached and main.py reads settings at import time. conftest module-level
code runs before any test module import, so the environment is prepared here,
at the top of this file.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "dev"

ADMIN_USER = "christina"
ADMIN_PASSWORD = "test-password"


def _free_port() -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


MOCK_PORT = _free_port()
STATE_DIR = tempfile.mkdtemp(prefix="hlidskjalf-test-state-")

# --- environment, before any hlidskjalf import ------------------------------

from argon2 import PasswordHasher  # backend runtime dep  # noqa: E402

os.environ.update(
    {
        "HLIDSKJALF_PVE_SCHEME": "http",
        "HLIDSKJALF_PVE_HOST": "127.0.0.1",
        "HLIDSKJALF_PVE_PORT": str(MOCK_PORT),
        "HLIDSKJALF_PVE_NODE": "hella",
        "HLIDSKJALF_PVE_TOKEN_SECRET": "mock-secret",
        "HLIDSKJALF_ADMIN_USER": ADMIN_USER,
        "HLIDSKJALF_ADMIN_PASSWORD_HASH": PasswordHasher().hash(ADMIN_PASSWORD),
        "HLIDSKJALF_SESSION_SECRET": "0123456789abcdef" * 4,
        "HLIDSKJALF_PROTECTED_VMIDS": "101,151",
        "HLIDSKJALF_RESCUE_ISO": "local:iso/systemrescue-12.01-amd64.iso",
        "HLIDSKJALF_BANDWIDTH_QUOTAS": '{"115": 500}',
        # Site-specific settings now default to empty (the panel ships neutral, not
        # wired to one homelab), so the suite states its own network explicitly.
        "HLIDSKJALF_VLAN_GATEWAYS": '{"20": "10.0.20.1", "30": "", "50": "10.0.50.1"}',
        "HLIDSKJALF_STATE_DIR": STATE_DIR,
        "HLIDSKJALF_STATIC_DIR": "",
        # cookie_secure defaults to True (production). Starlette's TestClient runs
        # over http and will NOT resend a Secure cookie, so disable it for tests.
        "HLIDSKJALF_COOKIE_SECURE": "false",
    }
)

from hlidskjalf.config import get_settings  # noqa: E402

get_settings.cache_clear()


# --- mock PVE server ---------------------------------------------------------


@pytest.fixture(scope="session")
def mock_pve_url() -> str:
    return f"http://127.0.0.1:{MOCK_PORT}"


@pytest.fixture(scope="session", autouse=True)
def mock_pve_server(mock_pve_url):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mock_pve:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(MOCK_PORT),
            "--log-level",
            "warning",
        ],
        cwd=DEV_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"mock_pve exited early: {proc.stderr.read().decode(errors='replace')}"
            )
        try:
            r = httpx.get(f"{mock_pve_url}/api2/json/cluster/resources", timeout=1.0)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            proc.terminate()
            raise RuntimeError("mock_pve did not become ready within 20s")
        time.sleep(0.1)
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


# --- panel app ---------------------------------------------------------------


@pytest.fixture(scope="session")
def client(mock_pve_server):
    """Session-scoped TestClient with the app lifespan running throughout."""
    from fastapi.testclient import TestClient

    from hlidskjalf.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """The login rate limiter is module-global state; isolate tests from it.

    It is now a per-IP dict of deques; reset_login_rate() clears every bucket.
    """
    from hlidskjalf import auth

    auth.reset_login_rate()
    yield
    auth.reset_login_rate()


@pytest.fixture()
def anon(client):
    """The session client with its cookie jar cleared (fresh, anonymous state).

    All requests must go through the one session-scoped TestClient: its portal
    owns the event loop the lifespan wired app.state clients into, and a second
    TestClient would run the app in a different loop.
    """
    client.cookies.clear()
    return client


@pytest.fixture()
def auth_client(anon):
    """Logged-in client; the CSRF token is stashed on `.csrf`."""
    r = anon.post(
        "/api/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    anon.csrf = r.json()["csrf"]
    return anon


def csrf_headers(c) -> dict:
    return {"X-Hlidskjalf-CSRF": c.csrf}
