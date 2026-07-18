"""Mock Arista eAPI server for testing the pure-eAPI AristaClient (dev only).

Simulates the JSON-RPC /command endpoint exactly as a real Arista 7050TX
(48x10G-T + 4x40G/100G model ports) would via:
  management api http-commands
    protocol http   # (for mock; real uses https)
    no shutdown

Supported cmds (in runCmds batch):
- "show interfaces status"
- "show interfaces description"
- "show interfaces counters rates"
- "show lldp neighbors"
- "show version"

Returns realistic structured JSON. ~48 physical Ethernet ports.
A subset are "connected" with descriptions, non-zero rates (some active),
and LLDP neighbors (system_name + neighbor_port) for connected servers/hosts.

Run (from dev/):
  uvicorn mock_switch:app --port 18080

To exercise against client (note: client hardcodes https + verify=False):
  # Quick test with curl (http ok for mock):
  curl -X POST http://127.0.0.1:18080/command \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show interfaces status","show interfaces description","show interfaces counters rates","show lldp neighbors"],"format":"json"},"id":"test"}'

Then in dev, you can temporarily edit switch.py base = f"http://..." for testing
or run a https wrapper. Set:
  HLIDSKJALF_SWITCH_HOST=127.0.0.1
  HLIDSKJALF_SWITCH_PORT=18080
  HLIDSKJALF_SWITCH_USERNAME=admin
  HLIDSKJALF_SWITCH_PASSWORD=admin
  (use_eapi remains true)

Matches structures used by AristaClient._get_ports_eapi().
"""

import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="mock-arista-eapi")

# Model: Arista DCS-7050TX-48 (48x 10GBASE-T + 4x 40GbE uplinks modeled)
# We expose Ethernet1..Ethernet52 for realism (client filters to ethernet*)

NUM_PORTS = 52
# Some ports "connected" (linkStatus)
CONNECTED = {1, 2, 3, 5, 7, 12, 15, 20, 22, 25, 30, 31, 35, 40, 48, 50}
# Ports with LLDP info (subset of connected)
LLDP_PORTS = {
    1: ("proxmox-pve", "Eth1/1"),
    3: ("vps-host-j", "enp3s0"),
    5: ("nas-truenas", "ix0"),
    12: ("kvm-build", "eno1"),
    20: ("switch-leaf2", "Ethernet1/1"),
    25: ("ap-uplink", "eth1"),
    30: ("pve-spare", "enp1s0f0"),
    35: ("storage-iscsi", "vmnic2"),
    48: ("core-router", "Eth1/49"),
    50: ("oob-mgmt", "mgmt0"),
}

# Static-ish descriptions (enhanced for task)
DESCRIPTIONS = {
    1: "to-pve-pve",
    2: "dev-workstation",
    3: "vps-alpha",
    4: "spare",
    5: "truenas-nas",
    6: "",
    7: "k8s-worker-1",
    8: "k8s-worker-2",
    9: "lab-pi",
    10: "",
    12: "build-server",
    15: "home-assistant",
    20: "leaf-switch",
    22: "printer",
    25: "wifi-ap-rack",
    30: "pve-spare-node",
    31: "ceph-mon-1",
    35: "iscsi-target",
    40: "backup-host",
    48: "uplink-to-core",
    50: "oob-access",
}

VLAN_MAP = {1: 20, 3: 20, 5: 30, 7: 20, 12: 20, 15: 20, 20: 10, 25: 20, 30: 20, 31: 30, 35: 40, 40: 20, 48: 99, 50: 1}

BASE_RATE = 10_000_000_000  # 10G

def _port_name(i: int) -> str:
    return f"Ethernet{i}"

def _now_rates() -> dict:
    # Slight variation so active detection and graphs look alive
    t = int(time.time())
    rates = {}
    for i in range(1, NUM_PORTS + 1):
        name = _port_name(i)
        if i in CONNECTED:
            # connected: some low, some high (active)
            if i in (1, 3, 12, 35, 48):
                in_r = int(850_000_000 + (t % 7) * 12_000_000)  # ~850-950 Mbps, active
                out_r = int(120_000_000 + (t % 5) * 8_000_000)
            else:
                in_r = int(12_000 + (t + i) % 4000)
                out_r = int(45_000 + (t * 2 + i) % 9000)
        else:
            in_r = 0
            out_r = 0
        rates[name] = {"inBitsRate": float(in_r), "outBitsRate": float(out_r)}
    return rates

def _status_data() -> dict:
    iface_statuses = {}
    for i in range(1, NUM_PORTS + 1):
        name = _port_name(i)
        connected = i in CONNECTED
        vlan = VLAN_MAP.get(i)
        iface_statuses[name] = {
            "linkStatus": "connected" if connected else "notconnect",
            "lineProtocolStatus": "up" if connected else "down",
            "bandwidth": BASE_RATE if i <= 48 else 40_000_000_000,
            "duplex": "duplexFull" if connected else "",
            "vlanInformation": {"vlanId": vlan} if vlan else {},
            "interfaceType": "10GBASE-T" if i <= 48 else "40GBASE-SR4",
        }
    return {"interfaceStatuses": iface_statuses}

def _desc_data() -> dict:
    descs = []
    for i in range(1, NUM_PORTS + 1):
        name = _port_name(i)
        desc = DESCRIPTIONS.get(i, "")
        descs.append({
            "interface": name,
            "description": desc,
            "interfaceStatus": "connected" if i in CONNECTED else "notconnect",
        })
    return {"interfaceDescriptions": descs}

def _counters_data() -> dict:
    return {"interfaces": _now_rates()}

def _lldp_data() -> dict:
    neighs = []
    for i, (sys_name, nport) in LLDP_PORTS.items():
        neighs.append({
            "port": _port_name(i),
            "neighborDevice": sys_name,
            "neighborPort": nport,
            "ttl": 120,
            "neighborPortDescription": DESCRIPTIONS.get(i, ""),
            "systemDescription": "Arista Networks EOS" if "switch" in sys_name else "Linux",
        })
    return {"lldpNeighbors": neighs}

def _version_data() -> dict:
    # `show version` JSON — modelName/serialNumber/version are the stable
    # fields every EOS release reports; the panel reads nothing else.
    return {
        "mfgName": "Arista",
        "modelName": "DCS-7050TX-48",
        "serialNumber": "MOCK-SN-0042",
        "version": "4.31.0F",
        "architecture": "i386",
        "uptime": 1234567.89,
        "memTotal": 4012345,
        "memFree": 1234567,
    }

CMD_MAP = {
    "show interfaces status": _status_data,
    "show interfaces description": _desc_data,
    "show interfaces counters rates": _counters_data,
    "show lldp neighbors": _lldp_data,
    "show version": _version_data,
}

@app.post("/command")
async def command_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}, status_code=400)

    params = body.get("params", {})
    cmds = params.get("cmds", [])
    results = []

    for cmd in cmds:
        # Normalize (real eAPI is case sensitive but tolerant)
        key = cmd.strip()
        if key in CMD_MAP:
            results.append(CMD_MAP[key]())
        else:
            # Unknown cmd: return empty-ish structure to avoid client crash
            results.append({})

    resp = {
        "jsonrpc": "2.0",
        "result": results,
        "id": body.get("id", "hlidskjalf-switch"),
    }
    return JSONResponse(resp)

@app.get("/health")
def health():
    return {"ok": True, "model": "DCS-7050TX-48", "ports": NUM_PORTS, "mock": "eapi"}

# For direct run convenience (python -m uvicorn or similar)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18080)