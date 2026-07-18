"""User management (admin only) + ownership helpers.

Regular users get exactly one VM (like a VPS customer).
Admins can create users, assign VMs, reset passwords, etc.

Tenant identity sync (optional, docs/pangolin.md): when
`pangolin_user_sync_active` and the new user carries an email, the panel
invites that email into the Pangolin org so the tenant can pass the panel
resource's Platform SSO wall. The invite link is handed to the admin exactly
once (never stored, never logged); the friend's Pangolin password is chosen
by the friend. Deleting the panel user removes the edge identity. All of it
is best-effort — a Pangolin outage never fails a panel user create/delete.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import journal
from pydantic import BaseModel, Field

import re
import secrets

from .. import auth
from ..auth import get_current_user, is_admin, rate_limited, require_csrf, require_session
from ..config import get_settings
from ..db import Db
from ..deps import get_db
from .. import pangolin

router = APIRouter()


MIN_PASSWORD_LEN = 8

# Light shape check only — deliverability is the invite's problem, not ours.
# (Avoids a hard dependency on email-validator for one field.)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _invite_tenant(db: Db, request: Request, actor: str, target: str, email: str) -> dict:
    """Invite `email` into the Pangolin org's tenant role for panel user `target`.

    Best-effort: on any PangolinError the user's state becomes 'error' (retry
    via the sync endpoint) and the result says so — the panel account stands
    regardless. On success the invite LINK is returned to the admin exactly
    once; only the invitation id is persisted (for cancel-on-delete).
    """
    s = get_settings()
    try:
        async with pangolin.PangolinClient(s) as client:
            role_id = await client.role_id_by_name(s.pangolin_tenant_role)
            invite = await client.create_invite(email, role_id)
    except pangolin.PangolinError as e:
        await db.set_user_pangolin_state(target, "error")
        await journal.record(db, request, actor, journal.PANGOLIN_INVITE, target,
                             f"FAILED: {e}")
        return {"state": "error", "error": str(e)}

    await db.set_user_pangolin_state(target, "invited", str(invite.get("inviteId") or ""))
    await journal.record(db, request, actor, journal.PANGOLIN_INVITE, target,
                         f"email={email} role={s.pangolin_tenant_role}")
    result: dict = {"state": "invited", "inviteLink": invite["inviteLink"]}
    if invite.get("expiresAt") is not None:
        result["expiresAt"] = invite["expiresAt"]
    return result


async def _offboard_tenant(db: Db, request: Request, actor: str, target: str, email: str, invite_id: str) -> None:
    """Remove the edge identity when the panel user goes away.

    The org user is looked up BY EMAIL — the invitee picks their username at
    accept time, and the invited email is the only stable key. That also makes
    the guard inherent: an account under a different email is never matched,
    so a pre-existing Pangolin user that happens to share a panel username is
    never touched. A never-accepted invite is cancelled instead. Failures are
    journaled for manual cleanup; the panel delete proceeds either way.
    """
    s = get_settings()
    try:
        async with pangolin.PangolinClient(s) as client:
            org_user = await client.get_user_by_email(email)
            if org_user is not None:
                uid = org_user.get("userId", org_user.get("id"))
                await client.delete_org_user(uid)
                await journal.record(db, request, actor, journal.PANGOLIN_OFFBOARD, target,
                                     "org user removed")
            elif invite_id:
                await client.delete_invitation(invite_id)
                await journal.record(db, request, actor, journal.PANGOLIN_OFFBOARD, target,
                                     "unaccepted invite cancelled")
    except pangolin.PangolinError as e:
        await journal.record(db, request, actor, journal.PANGOLIN_OFFBOARD, target,
                             f"FAILED: {e} — remove manually in the Pangolin dashboard")


class CreateUserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LEN)
    role: str = "user"  # "admin" or "user"
    vmid: int | None = None
    email: str | None = Field(default=None, max_length=254)  # for the Pangolin SSO invite


class PasswordBody(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LEN)
    # Required when changing your OWN password; ignored for an admin resetting
    # someone else's.
    current_password: str | None = None


class AssignVmidBody(BaseModel):
    vmid: int | None


@router.get("/api/users")
async def list_users(
    username: str = Depends(require_session),
    db: Db = Depends(get_db),
):
    me = await get_current_user(username, db)
    if not is_admin(me):
        raise HTTPException(403, "Admin only")
    return await db.list_users()


@router.post("/api/users", status_code=201)
async def create_user(
    body: CreateUserBody,
    request: Request,
    db: Db = Depends(get_db),
    _csrf=Depends(require_csrf),
    username: str = Depends(rate_limited("user.create", 20, 3600.0)),
):
    me = await get_current_user(username, db)
    if not is_admin(me):
        raise HTTPException(403, "Admin only")

    existing = await db.get_user_by_username(body.username)
    if existing:
        raise HTTPException(409, "Username already exists")

    email = (body.email or "").strip().lower()
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(400, "email does not look like an email address")

    if body.vmid is not None:
        # Prevent assigning same VM to two users
        users = await db.list_users()
        if any(u.get("vmid") == body.vmid for u in users):
            raise HTTPException(409, f"VM {body.vmid} is already assigned to another user")

    pw_hash = auth.hash_password(body.password)
    role = body.role if body.role in ("admin", "user") else "user"
    uid = await db.create_user(body.username, pw_hash, role, body.vmid, email=email)
    await journal.record(db, request, username, journal.USER_CREATE, body.username,
                         f"role={role} vmid={body.vmid}")
    result: dict = {"id": uid, "username": body.username, "role": role, "vmid": body.vmid}

    # Tenant identity sync: invite the email into the Pangolin org so the new
    # tenant can pass the panel resource's Platform SSO wall. Best-effort —
    # the panel account stands regardless of what Pangolin says.
    if email and get_settings().pangolin_user_sync_active:
        result["pangolin"] = await _invite_tenant(db, request, username, body.username, email)
    return result


@router.post("/api/users/{target}/password")
async def set_password(
    target: str,
    body: PasswordBody,
    request: Request,
    response: Response,
    db: Db = Depends(get_db),
    _csrf=Depends(require_csrf),
    username: str = Depends(rate_limited("user.password", 20, 3600.0)),
):
    me = await get_current_user(username, db)
    is_self = secrets.compare_digest(target, username)
    if not is_admin(me) and not is_self:
        raise HTTPException(403, "Can only change your own password (or be admin)")

    target_user = await db.get_user_by_username(target)
    if not target_user:
        raise HTTPException(404, f"No user named '{target}'")

    # Changing your own password requires proving you know the current one.
    # Otherwise a stolen session (cookie + CSRF) could be silently upgraded into
    # permanent credentials, and lock the real owner out. An admin resetting
    # *another* account is the legitimate recovery path and is exempt.
    if is_self:
        if not body.current_password or not auth.verify_password(
            target_user["password_hash"], body.current_password
        ):
            raise HTTPException(403, "Current password is incorrect")

    new_hash = auth.hash_password(body.password)
    await db.update_user_password(target, new_hash)
    # The password hash is the session epoch, so every session issued under the
    # old password (including any an attacker holds) is now invalid. Re-issue a
    # cookie for the caller when they changed their own password, so the person
    # who did the right thing isn't the one who gets logged out.
    if is_self:
        auth.start_session(response, username, auth.session_epoch(new_hash))
    await journal.record(db, request, username, journal.USER_PASSWORD, target,
                         "self" if is_self else "admin reset")
    return {"ok": True}


@router.post("/api/users/{target}/assign")
async def assign_vmid(
    target: str,
    body: AssignVmidBody,
    request: Request,
    db: Db = Depends(get_db),
    _csrf=Depends(require_csrf),
    username: str = Depends(require_session),
):
    me = await get_current_user(username, db)
    if not is_admin(me):
        raise HTTPException(403, "Admin only")

    if not await db.get_user_by_username(target):
        raise HTTPException(404, f"No user named '{target}'")

    if body.vmid is not None:
        users = await db.list_users()
        if any(u.get("username") != target and u.get("vmid") == body.vmid for u in users):
            raise HTTPException(409, f"VM {body.vmid} already assigned")

    await db.update_user_vmid(target, body.vmid)
    await journal.record(db, request, username, journal.USER_ASSIGN, target, f"vmid={body.vmid}")
    return {"ok": True, "username": target, "vmid": body.vmid}


@router.delete("/api/users/{target}")
async def delete_user(
    target: str,
    request: Request,
    db: Db = Depends(get_db),
    _csrf=Depends(require_csrf),
    username: str = Depends(require_session),
):
    me = await get_current_user(username, db)
    if not is_admin(me):
        raise HTTPException(403, "Admin only")

    target_user = await db.get_user_by_username(target)
    if not target_user:
        raise HTTPException(404, f"No user named '{target}'")

    # Never leave the panel without an admin. A sole admin can only be targeted
    # by themselves (any other admin acting would make the count >= 2), so this
    # guard fires before the generic self-delete guard below.
    if target_user.get("role") == "admin":
        admin_count = sum(1 for u in await db.list_users() if u.get("role") == "admin")
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the last admin")

    if secrets.compare_digest(target, username):
        raise HTTPException(400, "Cannot delete yourself")

    # Offboard the edge identity BEFORE the row disappears (we need the email
    # and invite id). Best-effort: the panel delete proceeds regardless.
    email = (target_user.get("email") or "").strip()
    if email and get_settings().pangolin_user_sync_active:
        await _offboard_tenant(db, request, username, target, email,
                               target_user.get("pangolin_invite_id") or "")

    await db.delete_user(target)
    await journal.record(db, request, username, journal.USER_DELETE, target)
    return {"ok": True}


@router.post("/api/users/{target}/pangolin-sync")
async def pangolin_sync(
    target: str,
    request: Request,
    db: Db = Depends(get_db),
    _csrf=Depends(require_csrf),
    username: str = Depends(require_session),
):
    """Retry or refresh the tenant's edge identity.

    - 'error' (or never synced) with an email on file → invite again.
    - 'invited' → probe the org; flips to 'active' once the friend accepted.
    """
    me = await get_current_user(username, db)
    if not is_admin(me):
        raise HTTPException(403, "Admin only")

    if not get_settings().pangolin_user_sync_active:
        raise HTTPException(400, "Pangolin user sync is not enabled")

    target_user = await db.get_user_by_username(target)
    if not target_user:
        raise HTTPException(404, f"No user named '{target}'")

    email = (target_user.get("email") or "").strip()
    state = target_user.get("pangolin_state") or ""

    if state == "invited":
        s = get_settings()
        try:
            async with pangolin.PangolinClient(s) as client:
                org_user = await client.get_user_by_email(email)
        except pangolin.PangolinError as e:
            raise HTTPException(502, f"Pangolin unreachable: {e}")
        if org_user is not None:
            await db.set_user_pangolin_state(target, "active")
            return {"state": "active"}
        return {"state": "invited"}  # not accepted yet — link still ticking

    if not email:
        raise HTTPException(400, "no email on file — the tenant cannot be invited")

    return await _invite_tenant(db, request, username, target, email)