"""Mock Pangolin Integration API for local Hlidskjalf development and tests.

Simulates the routes the panel's integrations use:

    PUT    /org/{orgId}/resource         -> create a TCP resource, returns resourceId
    PUT    /resource/{resourceId}/target -> attach a target (siteId, ip, port)
    DELETE /resource/{resourceId}         -> delete the resource

    GET    /org/{orgId}/roles            -> a fixed Admin/Member pair
    POST   /org/{orgId}/create-invite    -> invite an email, returns inviteLink
    GET    /org/{orgId}/user-by-username -> 404 until the invite is "accepted"
    DELETE /org/{orgId}/user/{userId}    -> remove an org user
    DELETE /org/{orgId}/invitations/{id} -> cancel an unaccepted invite

Plus a tiny GET /_state for tests to introspect what the panel actually created,
a POST /_accept/{email} hook that flips an invite into an org user (the friend
"clicked the link"), and one-shot failure hooks (POST /_fail_next/{target,
delete,invite,user_delete}) so tests can drive the panel's best-effort
degradation paths.
Generic: no real org ids, sites, ports or addresses. Bearer auth is accepted but
not checked (there is no real secret here).

Run:  uvicorn mock_pangolin:app --port 18443   (from dev/)
"""

from itertools import count

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="mock-pangolin")

resources: dict[int, dict] = {}
targets: dict[int, list[dict]] = {}
_resource_seq = count(1000)
_target_seq = count(5000)

# Identity sync state: invitations by inviteId, org users by userId.
ROLES = [{"roleId": 1, "name": "Admin"}, {"roleId": 2, "name": "Member"}]
invites: dict[int, dict] = {}
org_users: dict[int, dict] = {}
_invite_seq = count(7000)
_user_seq = count(9000)

# One-shot failure injection, so tests can drive the panel's best-effort
# degradation paths: arm a hook and the NEXT matching call fails once, then
# the hook clears itself.
_fail_next: set[str] = set()


@app.post("/_fail_next/{what}")
async def fail_next(what: str):
    if what not in ("target", "delete", "invite", "user_delete"):
        raise HTTPException(404, "unknown hook")
    _fail_next.add(what)
    return {"armed": what}


@app.put("/org/{org_id}/resource")
async def create_resource(org_id: str, request: Request):
    body = await request.json()
    resource_id = next(_resource_seq)
    resources[resource_id] = {
        "resourceId": resource_id,
        "org_id": org_id,
        "name": body.get("name"),
        "http": body.get("http"),
        "protocol": body.get("protocol"),
        "proxyPort": body.get("proxyPort"),
    }
    targets[resource_id] = []
    return {"data": resources[resource_id]}


@app.put("/resource/{resource_id}/target")
async def add_target(resource_id: int, request: Request):
    if "target" in _fail_next:
        _fail_next.discard("target")
        raise HTTPException(500, "injected target failure")
    body = await request.json()
    target_id = next(_target_seq)
    target = {
        "targetId": target_id,
        "resourceId": resource_id,
        "siteId": body.get("siteId"),
        "ip": body.get("ip"),
        "port": body.get("port"),
        "method": body.get("method"),
        "enabled": body.get("enabled"),
    }
    targets.setdefault(resource_id, []).append(target)
    return {"data": target}


@app.delete("/resource/{resource_id}")
async def delete_resource(resource_id: int):
    if "delete" in _fail_next:
        _fail_next.discard("delete")
        raise HTTPException(500, "injected delete failure")
    resources.pop(resource_id, None)
    targets.pop(resource_id, None)
    return {"data": None}


# --- tenant identity sync ----------------------------------------------------

@app.get("/org/{org_id}/roles")
async def list_roles(org_id: str):
    return {"data": {"roles": ROLES}}


@app.post("/org/{org_id}/create-invite")
async def create_invite(org_id: str, request: Request):
    if "invite" in _fail_next:
        _fail_next.discard("invite")
        raise HTTPException(500, "injected invite failure")
    body = await request.json()
    email = str(body.get("email", "")).lower()
    if not email or "@" not in email:
        raise HTTPException(400, "email required")
    invite_id = next(_invite_seq)
    invites[invite_id] = {
        "inviteId": invite_id,
        "org_id": org_id,
        "email": email,
        "roleId": body.get("roleId"),
        "sendEmail": body.get("sendEmail"),
        "validHours": body.get("validHours"),
    }
    return {
        "data": {
            "inviteId": invite_id,
            "inviteLink": f"https://pangolin.example.invalid/invite?token={invite_id}-mocktoken&email={email}",
            "expiresAt": 1_800_000_000_000,
        }
    }


@app.get("/org/{org_id}/users")
async def list_org_users(org_id: str, query: str = ""):
    """The panel looks invitees up BY EMAIL (they pick their username when
    accepting) — substring `query` filter like the real listUsers."""
    users = list(org_users.values())
    if query:
        q = query.lower()
        users = [u for u in users if q in u["username"].lower() or q in u["email"].lower()]
    return {"data": {"users": users}}


@app.get("/org/{org_id}/user-by-username")
async def user_by_username(org_id: str, username: str = ""):
    for u in org_users.values():
        if u["username"] == username.lower():
            return {"data": {"user": u}}
    raise HTTPException(404, "no such org user")


@app.delete("/org/{org_id}/user/{user_id}")
async def delete_org_user(org_id: str, user_id: int):
    if "user_delete" in _fail_next:
        _fail_next.discard("user_delete")
        raise HTTPException(500, "injected user delete failure")
    if user_id not in org_users:
        raise HTTPException(404, "no such org user")
    del org_users[user_id]
    return {"data": None}


@app.delete("/org/{org_id}/invitations/{invite_id}")
async def delete_invitation(org_id: str, invite_id: int):
    invites.pop(invite_id, None)
    return {"data": None}


@app.post("/_accept/{email}")
async def accept_invite(email: str):
    """Test hook: the friend "clicked the link" — flip their invite into an
    org user. Username = the email's local part (like a tenant choosing it)."""
    email = email.lower()
    for inv in invites.values():
        if inv["email"] == email:
            uid = next(_user_seq)
            org_users[uid] = {
                "userId": uid,
                "username": email.split("@")[0],
                "email": email,
                "roleId": inv["roleId"],
            }
            return {"data": org_users[uid]}
    raise HTTPException(404, "no invite for that email")


@app.get("/_state")
async def state():
    """Test-only introspection: what the panel created here."""
    return {
        "resources": list(resources.values()),
        "targets": targets,
        "invites": list(invites.values()),
        "org_users": list(org_users.values()),
    }
