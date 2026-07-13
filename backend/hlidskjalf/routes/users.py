"""User management (admin only) + ownership helpers.

Regular users get exactly one VM (like a VPS customer).
Admins can create users, assign VMs, reset passwords, etc.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import journal
from pydantic import BaseModel, Field

import secrets

from .. import auth
from ..auth import get_current_user, is_admin, rate_limited, require_csrf, require_session
from ..db import Db
from ..deps import get_db

router = APIRouter()


MIN_PASSWORD_LEN = 8


class CreateUserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LEN)
    role: str = "user"  # "admin" or "user"
    vmid: int | None = None


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

    if body.vmid is not None:
        # Prevent assigning same VM to two users
        users = await db.list_users()
        if any(u.get("vmid") == body.vmid for u in users):
            raise HTTPException(409, f"VM {body.vmid} is already assigned to another user")

    pw_hash = auth.hash_password(body.password)
    role = body.role if body.role in ("admin", "user") else "user"
    uid = await db.create_user(body.username, pw_hash, role, body.vmid)
    await journal.record(db, request, username, journal.USER_CREATE, body.username,
                         f"role={role} vmid={body.vmid}")
    return {"id": uid, "username": body.username, "role": role, "vmid": body.vmid}


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

    await db.delete_user(target)
    await journal.record(db, request, username, journal.USER_DELETE, target)
    return {"ok": True}