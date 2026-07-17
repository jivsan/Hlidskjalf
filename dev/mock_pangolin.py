"""Mock Pangolin Integration API for local Hlidskjalf development and tests.

Simulates just the three routes the panel's SSH-tunnel integration uses:

    PUT    /org/{orgId}/resource        -> create a TCP resource, returns resourceId
    PUT    /resource/{resourceId}/target -> attach a target (siteId, ip, port)
    DELETE /resource/{resourceId}        -> delete the resource

Plus a tiny GET /_state for tests to introspect what the panel actually created.
Generic: no real org ids, sites, ports or addresses. Bearer auth is accepted but
not checked (there is no real secret here).

Run:  uvicorn mock_pangolin:app --port 18443   (from dev/)
"""

from itertools import count

from fastapi import FastAPI, Request

app = FastAPI(title="mock-pangolin")

resources: dict[int, dict] = {}
targets: dict[int, list[dict]] = {}
_resource_seq = count(1000)
_target_seq = count(5000)


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
    resources.pop(resource_id, None)
    targets.pop(resource_id, None)
    return {"data": None}


@app.get("/_state")
async def state():
    """Test-only introspection: what the panel created here."""
    return {"resources": list(resources.values()), "targets": targets}
