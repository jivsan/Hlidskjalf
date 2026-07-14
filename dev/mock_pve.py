"""Mock Proxmox VE API for local Hlidskjalf development.

Simulates just enough of the PVE REST surface (plain HTTP, no auth check) to
exercise every panel feature except the live VNC console: fleet, detail,
rrddata graphs, bandwidth accumulation (counters tick), power actions, tasks,
clone/provision/destroy, rescue config writes.

Run:  uvicorn mock_pve:app --port 18006   (from dev/)
Then: HLIDSKJALF_PVE_SCHEME=http HLIDSKJALF_PVE_HOST=127.0.0.1 \
      HLIDSKJALF_PVE_PORT=18006 ... uvicorn hlidskjalf.main:app --port 8787
"""

import math
import random
import time
from itertools import count

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

app = FastAPI(title="mock-pve")
NODE = "pve"
BOOT = time.time()

vms: dict[int, dict] = {
    101: dict(name="panel-host", type="qemu", status="running", cores=4, memory=8192,
              maxdisk=64 << 30, vlan="20", ip="192.168.20.17"),
    105: dict(name="vps-alpha", type="qemu", status="running", cores=8, memory=16384,
              maxdisk=200 << 30, vlan="20", ip="192.168.20.15"),
    115: dict(name="vps-beta", type="qemu", status="running", cores=2, memory=4096,
              maxdisk=40 << 30, vlan="50", ip="192.168.50.11"),
    120: dict(name="app-01", type="qemu", status="running", cores=2, memory=4096,
              maxdisk=32 << 30, vlan="20", ip="192.168.20.30"),
    151: dict(name="pbs", type="qemu", status="running", cores=2, memory=4096,
              maxdisk=500 << 30, vlan="30", ip="192.168.30.5"),
    130: dict(name="ct-runner", type="lxc", status="running", cores=2, memory=2048,
              maxdisk=16 << 30, vlan="20", ip="192.168.20.40"),
    140: dict(name="scratch-old", type="qemu", status="stopped", cores=1, memory=1024,
              maxdisk=8 << 30, vlan="20", ip="192.168.20.90"),
    9000: dict(name="debian13-template", type="qemu", status="stopped", template=1,
               cores=2, memory=2048, maxdisk=4 << 30, vlan="20", ip=""),
    9001: dict(name="ubuntu2404-template", type="qemu", status="stopped", template=1,
               cores=2, memory=2048, maxdisk=4 << 30, vlan="20", ip=""),
}
# per-VM synthetic traffic rates (bytes/sec) so the accumulator sees motion
rates = {vmid: (random.randint(20_000, 4_000_000), random.randint(10_000, 1_500_000))
         for vmid in vms}
started_at = {vmid: BOOT - random.randint(3600, 40 * 86400)
              for vmid, v in vms.items() if v["status"] == "running"}
extra_config: dict[int, dict] = {}
tasks: dict[str, dict] = {}
_upid_seq = count(1)


def _mk_upid(type_: str, vmid: int) -> str:
    # Real PVE: UPID:node:pid:pstart:starttime:dtype:id:user:   -> 9 colon-fields.
    # This mock used to omit `pstart`, emitting 8 — which put the *user* where the
    # vmid should be, so routes/vms.py::_vmid_from_upid could not read a vmid and
    # (correctly, failing closed) treated every guest task as node-level and
    # admin-only. A regular user could not poll the task for their own power
    # action. No test caught it because the tests hand-wrote *correct* UPIDs
    # instead of the ones this mock actually hands out.
    now = int(time.time())
    pid = f"0000{next(_upid_seq):04X}"
    pstart = f"{now & 0xFFFFFFFF:08X}"
    upid = f"UPID:{NODE}:{pid}:{pstart}:{now:08X}:{type_}:{vmid}:mock@pve:"
    tasks[upid] = {"upid": upid, "type": type_, "id": str(vmid), "user": "mock@pve",
                   "starttime": int(time.time()), "status": "running", "node": NODE,
                   "done_at": time.time() + 1.5}
    return upid


def _task_view(t: dict) -> dict:
    t = dict(t)
    if t["status"] == "running" and time.time() >= t["done_at"]:
        t["status"] = "stopped"
        t["exitstatus"] = "OK"
        t["endtime"] = int(t["done_at"])
        tasks[t["upid"]].update(t)
    t.pop("done_at", None)
    return t


def _counters(vmid: int) -> tuple[int, int]:
    if vms[vmid]["status"] != "running":
        return 0, 0
    dt = time.time() - started_at.get(vmid, BOOT)
    rin, rout = rates[vmid]
    return int(dt * rin), int(dt * rout)


def _guest_disk(v: dict, running: bool) -> int:
    # Real PVE (validated against a 9.2.3 host, 2026-07-13) reports disk=0 for
    # QEMU guests in /cluster/resources, status/current AND rrddata: the
    # hypervisor cannot see in-guest filesystem usage (the guest agent is not
    # consulted for this figure). Only LXC containers report real disk usage.
    # This mock used to fabricate 45% of maxdisk for everything, so the UI's
    # disk bar looked plausible in dev and read empty against reality.
    if v["type"] == "qemu":
        return 0
    return int(v["maxdisk"] * 0.45) if running else 0


def _resource(vmid: int, v: dict) -> dict:
    running = v["status"] == "running"
    netin, netout = _counters(vmid)
    uptime = int(time.time() - started_at[vmid]) if running and vmid in started_at else 0
    phase = (vmid * 37) % 100
    cpu = (0.05 + 0.4 * abs(math.sin(time.time() / 300 + phase))) if running else 0
    return {
        "vmid": vmid, "name": v["name"], "type": v["type"], "node": NODE,
        "status": v["status"], "template": v.get("template", 0),
        "cpu": round(cpu, 4), "maxcpu": v["cores"],
        "mem": int(v["memory"] * (1 << 20) * (0.3 + 0.4 * abs(math.sin(time.time() / 600 + phase)))) if running else 0,
        "maxmem": v["memory"] * (1 << 20),
        "disk": _guest_disk(v, running), "maxdisk": v["maxdisk"],
        "uptime": uptime, "netin": netin, "netout": netout,
        "diskread": int(uptime * 80_000), "diskwrite": int(uptime * 40_000),
        "tags": "",
    }


@app.get("/api2/json/nodes")
async def nodes():
    """Real PVE exposes this; the setup wizard uses it to confirm the node exists."""
    return {
        "data": [
            {
                "node": NODE,
                "status": "online",
                "type": "node",
                "uptime": 2_600_000,
            }
        ]
    }


@app.get("/api2/json/cluster/resources")
async def cluster_resources(type: str | None = None):
    return {"data": [_resource(vmid, v) for vmid, v in sorted(vms.items())]}


def _guest(vmid: int) -> dict:
    if vmid not in vms:
        raise HTTPException(404, "guest does not exist")
    return vms[vmid]


@app.get("/api2/json/nodes/{node}/{kind}/{vmid}/status/current")
async def status_current(node: str, kind: str, vmid: int):
    v = _guest(vmid)
    r = _resource(vmid, v)
    r["agent"] = 1 if v["type"] == "qemu" and v["status"] == "running" else 0
    r["cpus"] = v["cores"]
    return {"data": r}


@app.get("/api2/json/nodes/{node}/{kind}/{vmid}/config")
async def vm_config(node: str, kind: str, vmid: int):
    v = _guest(vmid)
    cfg = {
        "name": v["name"], "cores": v["cores"], "memory": str(v["memory"]),
        "net0": f"virtio=BC:24:11:{vmid:02X}:AA:01,bridge=vmbr0,tag={v['vlan']},firewall=0",
        "scsi0": f"local-lvm:vm-{vmid}-disk-0,size={v['maxdisk'] >> 30}G",
        "boot": "order=scsi0", "onboot": 1, "ostype": "l26", "agent": "enabled=1",
        "ide2": "local-lvm:vm-{}-cloudinit,media=cdrom".format(vmid),
    }
    if v["ip"]:
        gw = ".".join(v["ip"].split(".")[:3]) + ".1"
        cfg["ipconfig0"] = f"ip={v['ip']}/24,gw={gw}"
    cfg.update(extra_config.get(vmid, {}))
    return {"data": cfg}


@app.put("/api2/json/nodes/{node}/{kind}/{vmid}/config")
async def set_config(node: str, kind: str, vmid: int, request: Request):
    _guest(vmid)
    form = dict(await request.form())
    for key in str(form.pop("delete", "")).split(","):
        extra_config.setdefault(vmid, {}).pop(key, None)
    extra_config.setdefault(vmid, {}).update(form)
    return {"data": None}


@app.put("/api2/json/nodes/{node}/{kind}/{vmid}/resize")
async def resize(node: str, kind: str, vmid: int, request: Request):
    form = dict(await request.form())
    size = str(form.get("size", "0G"))
    vms[vmid]["maxdisk"] = int(size.rstrip("G")) << 30
    return {"data": None}


@app.post("/api2/json/nodes/{node}/{kind}/{vmid}/status/{action}")
async def power(node: str, kind: str, vmid: int, action: str):
    v = _guest(vmid)
    if action == "start":
        v["status"] = "running"
        started_at[vmid] = time.time()
        rates.setdefault(vmid, (100_000, 50_000))
    elif action in ("shutdown", "stop"):
        v["status"] = "stopped"
    elif action in ("reboot", "reset"):
        started_at[vmid] = time.time()  # counters reset — exercises the reset rule
    return {"data": _mk_upid(f"qm{action}", vmid)}


@app.post("/api2/json/nodes/{node}/qemu/{vmid}/clone")
async def clone(node: str, vmid: int, request: Request):
    form = dict(await request.form())
    newid = int(str(form["newid"]))
    if newid in vms:
        raise HTTPException(400, "VMID already exists")
    tpl = _guest(vmid)
    vms[newid] = dict(tpl, name=str(form.get("name", f"vm{newid}")), template=0,
                      status="stopped", ip="")
    rates[newid] = (random.randint(20_000, 500_000), random.randint(10_000, 200_000))
    return {"data": _mk_upid("qmclone", newid)}


@app.delete("/api2/json/nodes/{node}/{kind}/{vmid}")
async def destroy(node: str, kind: str, vmid: int):
    _guest(vmid)
    del vms[vmid]
    extra_config.pop(vmid, None)
    return {"data": _mk_upid("qmdestroy", vmid)}


@app.get("/api2/json/nodes/{node}/tasks/{upid}/status")
async def task_status(node: str, upid: str):
    if upid not in tasks:
        raise HTTPException(404, "no such task")
    return {"data": _task_view(tasks[upid])}


@app.get("/api2/json/nodes/{node}/tasks")
async def task_list(node: str, limit: int = 50):
    rows = [_task_view(t) for t in tasks.values()]
    rows.sort(key=lambda t: -t["starttime"])
    return {"data": rows[:limit]}


@app.get("/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
async def agent_net(node: str, vmid: int):
    v = _guest(vmid)
    if v["status"] != "running":
        raise HTTPException(500, "agent not running")
    return {"data": {"result": [
        {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
        {"name": "eth0", "ip-addresses": [{"ip-address": v["ip"] or "192.168.20.99",
                                           "ip-address-type": "ipv4"}]},
    ]}}


def _rrd_rows(timeframe: str):
    spans = {"hour": (70, 60), "day": (70, 1800), "week": (70, 7200),
             "month": (70, 43200), "year": (70, 604800)}
    n, step = spans.get(timeframe, (70, 60))
    now = int(time.time())
    return [(now - (n - i) * step) for i in range(n)]


@app.get("/api2/json/nodes/{node}/{kind}/{vmid}/rrddata")
async def vm_rrd(node: str, kind: str, vmid: int, timeframe: str = "hour", cf: str = "AVERAGE"):
    v = _guest(vmid)
    rin, rout = rates.get(vmid, (100_000, 50_000))
    rows = []
    for t in _rrd_rows(timeframe):
        s = abs(math.sin(t / 3000 + vmid))
        rows.append({
            "time": t, "cpu": 0.05 + 0.5 * s, "maxcpu": v["cores"],
            "mem": v["memory"] * (1 << 20) * (0.3 + 0.3 * s), "maxmem": v["memory"] * (1 << 20),
            "disk": _guest_disk(v, v["status"] == "running"), "maxdisk": v["maxdisk"],
            "diskread": 90_000 * s, "diskwrite": 50_000 * s,
            "netin": rin * (0.4 + 0.8 * s), "netout": rout * (0.4 + 0.8 * s),
        })
    return {"data": rows}


@app.get("/api2/json/nodes/{node}/rrddata")
async def node_rrd(node: str, timeframe: str = "hour", cf: str = "AVERAGE"):
    rows = []
    for t in _rrd_rows(timeframe):
        s = abs(math.sin(t / 4000))
        rows.append({
            "time": t, "cpu": 0.1 + 0.4 * s, "maxcpu": 16,
            "memused": (24 + 20 * s) * (1 << 30), "memtotal": 64 << 30,
            "iowait": 0.02 * s, "netin": 5_000_000 * s, "netout": 2_000_000 * s,
            "loadavg": 0.5 + 3 * s, "rootused": 30 << 30, "roottotal": 100 << 30,
        })
    return {"data": rows}


@app.get("/api2/json/nodes/{node}/status")
async def node_status(node: str):
    return {"data": {
        "cpu": 0.18, "uptime": int(time.time() - BOOT + 86400 * 30),
        "loadavg": ["0.42", "0.51", "0.48"],
        # Simulate real PVE shape: no flat maxcpu/mem/maxmem at top level.
        # Cores live in cpuinfo; memory in nested object.
        "cpuinfo": {"cpus": 16, "cores": 8},
        "memory": {"used": 26 << 30, "total": 64 << 30, "free": 38 << 30},
        "rootfs": {"used": 30 << 30, "total": 100 << 30},
        "pveversion": "pve-manager/8.3.0 (mock)",
        "kversion": "Linux 6.8.12-mock",
    }}


@app.get("/api2/json/nodes/{node}/storage")
async def node_storage(node: str):
    return {"data": [
        {"storage": "local-lvm", "type": "lvmthin", "used": 210 << 30, "total": 800 << 30,
         "avail": 590 << 30, "content": "images,rootdir", "active": 1},
        {"storage": "local", "type": "dir", "used": 40 << 30, "total": 100 << 30,
         "avail": 60 << 30, "content": "iso,vztmpl,backup", "active": 1},
        # Real hosts rarely have exactly one image-capable storage (the first
        # real one had four); a second entry keeps the Settings select honest.
        {"storage": "vm-store", "type": "zfspool", "used": 300 << 30, "total": 1800 << 30,
         "avail": 1500 << 30, "content": "images,rootdir", "active": 1},
    ]}


@app.get("/api2/json/nodes/{node}/network")
async def node_network(node: str):
    # Real PVE shape (9.2.3): a list of interface dicts with `iface` + `type`;
    # bridges carry type == "bridge". More than one bridge is the norm — the
    # first real host runs its guests on vmbr1, not vmbr0.
    return {"data": [
        {"iface": "eno1", "type": "eth", "active": 1, "exists": 1, "method": "manual"},
        {"iface": "vmbr0", "type": "bridge", "active": 1, "autostart": 1,
         "method": "static", "cidr": "192.168.10.2/24", "address": "192.168.10.2",
         "netmask": "24", "gateway": "192.168.10.1", "bridge_ports": "eno1",
         "bridge_stp": "off", "bridge_fd": "0"},
        {"iface": "vmbr1", "type": "bridge", "active": 1, "autostart": 1,
         "method": "manual", "bridge_ports": "eno1.20", "bridge_stp": "off",
         "bridge_fd": "0", "bridge_vlan_aware": 1},
    ]}


@app.post("/api2/json/nodes/{node}/{kind}/{vmid}/vncproxy")
async def vncproxy(node: str, kind: str, vmid: int):
    return {"data": {"port": "5900", "ticket": "MOCK-TICKET-" + str(vmid),
                     "user": "mock@pve", "cert": ""}}


@app.post("/api2/json/nodes/{node}/{kind}/{vmid}/termproxy")
async def termproxy(node: str, kind: str, vmid: int):
    """The console endpoint real Proxmox uses for CONTAINERS.

    Validated on PVE 9.2.3 (2026-07-13): an LXC guest's `vncproxy` completes the
    RFB handshake and then hangs forever at ClientInit, while `termproxy` yields
    a live shell. Note `user` — termproxy needs it and vncproxy does not: the
    auth line is "<user>:<ticket>".
    """
    return {"data": {"port": "5901", "ticket": "MOCK-TERM-TICKET-" + str(vmid),
                     "user": "mock@pve!panel", "upid": _mk_upid("vncproxy", vmid)}}


@app.websocket("/api2/json/nodes/{node}/{kind}/{vmid}/vncwebsocket")
async def vncwebsocket(
    websocket: WebSocket,
    node: str,
    kind: str,
    vmid: int,
    port: str = "",
    vncticket: str = "",
):
    """Echo console websocket — VNC bytes for qemu, a termproxy stream for lxc.

    The real endpoint bridges to the guest's VNC server (qemu) or its terminal
    (lxc, the port termproxy handed out). The mock accepts the `binary`
    subprotocol (tolerating the port/vncticket query params and the PVEAPIToken
    header the panel sends) and echoes frames back, so an integration test can
    prove the panel's bidirectional pump moves data in both directions.

    For a container it first demands termproxy's "<user>:<ticket>" auth line and
    answers "OK", exactly as real PVE does. The panel sends that line itself (so
    the ticket never reaches the browser) — this is what keeps the suite honest
    about it.
    """
    # Negotiate, don't assert (RFC 6455 §4.1) — real PVE echoes back "binary"
    # only because the panel offers it. A server that selects a subprotocol the
    # client never offered gets its connection killed by the client.
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in offered else None)
    try:
        if kind == "lxc":
            auth = await websocket.receive()
            line = auth.get("text") or (auth.get("bytes") or b"").decode()
            if ":" not in line:
                await websocket.close(code=1008)
                return
            await websocket.send_text("OK")
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await websocket.send_bytes(msg["bytes"])
            else:
                await websocket.send_text(msg.get("text", ""))
    except WebSocketDisconnect:
        pass
