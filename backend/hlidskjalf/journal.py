"""The audit trail.

This panel can stop, reinstall and permanently destroy other people's machines.
Doing that with no durable record of who did it — the debug ring buffer lived in
memory and died on restart — is not something you can hand to another person and
call finished.

Every state-changing action records: when, who, what, to which target, from which
IP, and whether it succeeded. Refusals are recorded too: a denied destroy is
exactly the thing you want to find later.

Never put a secret in `detail`.
"""

import logging

from fastapi import Request

from .db import Db

log = logging.getLogger("hlidskjalf.audit")

# --- action names (keep these stable; they are queried) ---
LOGIN = "auth.login"
LOGIN_FAILED = "auth.login_failed"
LOGOUT = "auth.logout"
SETUP = "setup.complete"

VM_POWER = "vm.power"
VM_PROVISION = "vm.provision"
VM_REINSTALL = "vm.reinstall"
VM_DESTROY = "vm.destroy"
VM_RESCUE_ENTER = "vm.rescue_enter"
VM_RESCUE_EXIT = "vm.rescue_exit"

USER_CREATE = "user.create"
USER_DELETE = "user.delete"
USER_PASSWORD = "user.password"
USER_ASSIGN = "user.assign"

PANGOLIN_INVITE = "pangolin.invite"      # tenant edge-identity invited — NEVER log the link
PANGOLIN_OFFBOARD = "pangolin.offboard"  # tenant edge-identity removed (or failed to be)

SWITCH_NOTE = "switch.note"

SETTINGS_UPDATE = "settings.update"
PVE_CONNECTION = "settings.pve_connection"  # repointing the panel at a Proxmox — audit it loudly
PANEL_UPDATE = "panel.update"   # the panel updating its own code — audit it loudly


def client_ip(request: Request | None) -> str:
    """The address the request really came from.

    Behind a reverse proxy the socket peer is the proxy, so an audit log built on
    it records "127.0.0.1" for every action anyone ever takes — worthless exactly
    when the panel gains users who are not you. netzone.client_ip believes the
    forwarded headers only when the peer is a proxy the operator declared.
    """
    from .config import get_settings
    from .netzone import client_ip as real_client_ip

    s = get_settings()
    return real_client_ip(request, s.trusted_proxies, s.cloudflare)


async def record(
    db: Db,
    request: Request | None,
    actor: str,
    action: str,
    target: str | int | None = None,
    detail: str | None = None,
    ok: bool = True,
) -> None:
    """Write one audit row. Never raises — a failure to log must not break the action."""
    try:
        await db.audit(
            actor=actor,
            action=action,
            target=str(target) if target is not None else None,
            detail=detail,
            client=client_ip(request),
            ok=ok,
        )
    except Exception:  # pragma: no cover — logging must never take the request down
        log.exception("failed to write audit row for %s by %s", action, actor)
