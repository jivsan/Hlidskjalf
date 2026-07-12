"""Mock Arista eAPI for dev switch visualizer.

uvicorn dev.mock_switch:app --port 18080
"""

from fastapi import FastAPI, Request
import uvicorn
import random

app = FastAPI()

PORTS = [f"Ethernet{i}" for i in range(1, 53)]

@app.post("/command")
async def cmd(req: Request):
    body = await req.json()
    cmds = body.get("params", {}).get("cmds", [])
    res = []
    for c in cmds:
        if "status" in c:
            res.append({"interfaceStatuses": {p: {"linkStatus": "connected" if i % 3 else "notconnect", "bandwidth": "10G", "duplex": "full", "vlanInformation": {"vlanId": 20}} for i, p in enumerate(PORTS)}})
        elif "description" in c:
            res.append({"interfaceDescriptions": [{"interface": p, "description": f"to-host-{i%8}"} for i, p in enumerate(PORTS)]})
        elif "rates" in c:
            res.append({"interfaces": {p: {"inBitsRate": random.randint(0, 8000000), "outBitsRate": random.randint(0, 3000000)} for p in PORTS}})
        elif "lldp" in c:
            res.append({"lldpNeighbors": [{"port": p, "neighborDevice": f"machine-{i}", "neighborPort": "eth0"} for i, p in enumerate(PORTS) if i % 5 == 0]})
        else:
            res.append({})
    return {"jsonrpc": "2.0", "result": res, "id": body.get("id")}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18080)