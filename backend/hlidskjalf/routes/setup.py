"""First-run setup wizard.

The panel ships unconfigured. Until an account exists, these endpoints let an
operator point it at their Proxmox, create the admin, and optionally create a
first regular user — then they are signed straight in.

SECURITY — the whole design rests on one invariant:

    setup is available IFF the users table is empty.

These endpoints are unauthenticated (they must be — nobody has credentials yet),
so the moment any user exists they must refuse forever, or they would be a
permanent unauthenticated takeover backdoor. Every handler re-checks the gate,
and `POST /api/setup` re-checks it again *inside* the write path so two racing
requests cannot both create an admin. They are rate-limited per IP like login.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..config import SETUP_WRITABLE, get_settings
from ..db import Db
from ..deps import get_db
from ..pve import PveClient, PveError

log = logging.getLogger("hlidskjalf.setup")
router = APIRouter()

MIN_PASSWORD_LEN = 8


class PveConn(BaseModel):
    host: str = Field(min_length=1)
    port: int = 8006
    node: str = Field(min_length=1)
    scheme: str = "https"
    token_id: str = Field(min_length=1)
    token_secret: str = Field(min_length=1)
    fingerprint: str = ""
    verify_tls: bool = True


class Account(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LEN)


class FirstUser(Account):
    vmid: int | None = None


class SetupBody(BaseModel):
    pve: PveConn
    admin: Account
    user: FirstUser | None = None


async def _setup_needed(db: Db) -> bool:
    return not await db.list_users()


async def _require_setup_open(db: Db) -> None:
    if not await _setup_needed(db):
        raise HTTPException(409, "Setup has already been completed")


def _probe_settings(conn: PveConn):
    """A throwaway Settings carrying only the connection under test."""
    s = get_settings().model_copy()
    object.__setattr__(s, "pve_host", conn.host)
    object.__setattr__(s, "pve_port", conn.port)
    object.__setattr__(s, "pve_node", conn.node)
    object.__setattr__(s, "pve_scheme", conn.scheme)
    object.__setattr__(s, "pve_token_id", conn.token_id)
    object.__setattr__(s, "pve_token_secret", conn.token_secret)
    object.__setattr__(s, "pve_fingerprint", conn.fingerprint)
    return s


async def _probe(conn: PveConn) -> dict:
    """Talk to Proxmox with the supplied credentials. Raises HTTPException(400)."""
    if conn.scheme == "https" and not conn.fingerprint and conn.verify_tls:
        # pve.py refuses https without a fingerprint (it pins by design). Say so
        # plainly rather than letting the client see an opaque startup error.
        raise HTTPException(
            400,
            "An https connection needs the Proxmox certificate fingerprint. Run "
            "`openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256` "
            "on the host and paste the value.",
        )
    client = PveClient(_probe_settings(conn))
    try:
        nodes = await client.get("/nodes") or []
        names = [n.get("node") for n in nodes if n.get("node")]
        if conn.node not in names:
            raise HTTPException(
                400,
                f"Connected, but this Proxmox has no node named '{conn.node}'. "
                f"Found: {', '.join(names) or 'none'}.",
            )
        resources = await client.cluster_resources() or []
        # Hand back the actual guests, so the wizard can offer a picker for the
        # first user's VM instead of asking someone to type a VMID from memory.
        guest_list = sorted(
            (
                {"vmid": r["vmid"], "name": r.get("name") or f"vm {r['vmid']}"}
                for r in resources
                if r.get("type") in ("qemu", "lxc") and r.get("vmid") is not None
            ),
            key=lambda g: g["vmid"],
        )
        return {
            "ok": True,
            "node": conn.node,
            "guests": len(guest_list),
            "guest_list": guest_list,
            "nodes": names,
        }
    except HTTPException:
        raise
    except PveError as e:
        raise HTTPException(400, f"Proxmox rejected the credentials: {e}")
    except Exception as e:  # connection refused, TLS pin mismatch, DNS, timeout…
        raise HTTPException(400, f"Could not reach Proxmox at {conn.host}:{conn.port} — {e}")
    finally:
        await client.aclose()


@router.get("/api/setup/status")
async def setup_status(db: Db = Depends(get_db)):
    """Always reachable. Reveals only whether setup is required."""
    return {"needed": await _setup_needed(db)}


@router.post("/api/setup/test")
async def setup_test(conn: PveConn, request: Request, db: Db = Depends(get_db)):
    """Dry run — validate the connection without persisting anything."""
    await _require_setup_open(db)
    auth.check_login_rate(request.client.host if request.client else "-")
    return await _probe(conn)


@router.post("/api/setup")
async def setup_commit(
    body: SetupBody,
    request: Request,
    response: Response,
    db: Db = Depends(get_db),
):
    await _require_setup_open(db)
    auth.check_login_rate(request.client.host if request.client else "-")

    if body.user and body.user.username == body.admin.username:
        raise HTTPException(400, "The first user needs a different username to the admin")

    # Prove the connection works BEFORE persisting anything — a half-configured
    # panel that can't reach Proxmox is worse than one that is still unconfigured.
    await _probe(body.pve)

    settings = get_settings()
    stored: dict[str, str] = {
        "pve_host": body.pve.host,
        "pve_port": str(body.pve.port),
        "pve_node": body.pve.node,
        "pve_scheme": body.pve.scheme,
        "pve_token_id": body.pve.token_id,
        "pve_token_secret": body.pve.token_secret,
        "pve_fingerprint": body.pve.fingerprint,
    }
    # Without a session secret nothing can be signed. Mint a strong one on first
    # run so the operator never has to think about it.
    if not settings.session_secret:
        stored["session_secret"] = auth.new_session_secret()

    assert set(stored) <= SETUP_WRITABLE  # the allowlist is the security boundary

    # Create the admin FIRST: username is UNIQUE, so if two setup requests race,
    # exactly one wins here and the loser's _require_setup_open re-check fails.
    if not await _setup_needed(db):
        raise HTTPException(409, "Setup has already been completed")
    admin_hash = auth.hash_password(body.admin.password)
    try:
        await db.create_user(body.admin.username, admin_hash, "admin", None)
    except Exception:
        raise HTTPException(409, "Setup has already been completed")

    # The Proxmox token and the session key are encrypted at rest (secretbox.py);
    # they are never written to the database in plaintext. The in-memory settings
    # keep the plaintext, which is what actually talks to Proxmox.
    from ..config import apply_stored, seal

    await db.set_config(seal(stored, settings))
    apply_stored(settings, stored)

    if body.user:
        await db.create_user(
            body.user.username,
            auth.hash_password(body.user.password),
            "user",
            body.user.vmid,
        )

    # Bring the PVE client / metrics / accumulator up against the configuration we
    # just committed, so the panel works immediately without a restart.
    from ..main import start_pve_stack, stop_pve_stack

    await stop_pve_stack(request.app)
    await start_pve_stack(request.app, settings)

    log.info("setup complete — watching node %s as %s", body.pve.node, body.admin.username)

    # Sign the admin in: they just proved ownership by configuring the panel.
    epoch = auth.session_epoch(admin_hash)
    csrf = auth.start_session(response, body.admin.username, epoch)
    return {
        "ok": True,
        "csrf": csrf,
        "user": body.admin.username,
        "role": "admin",
        "vmid": None,
        "node": body.pve.node,
    }
