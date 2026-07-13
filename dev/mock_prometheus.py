"""Mock Prometheus HTTP API for local Hlidskjalf development and tests.

Serves just enough of `/api/v1/query_range` to exercise
`hlidskjalf/datasources/prometheus.py` offline: it recognises the
prometheus-pve-exporter metric names inside a PromQL expression, reads the
`id="qemu/105"` label off it, and answers with a plausible `matrix` response.

It does NOT evaluate PromQL. `rate(pve_network_receive_bytes{...}[300s])` is
answered with values already in bytes/sec (i.e. what a real Prometheus would
compute), and `avg_over_time(pve_cpu_usage_ratio{...}[60s])` with the ratio —
the wrapper functions are ignored, only the inner metric name matters.

Deliberate gaps, so the None-fill path is exercised:
- unknown metric (e.g. node_load1 with no override configured) -> empty result
- unknown guest id                                             -> empty result
- pve_disk_usage_bytes for an `lxc/*` id                       -> empty result

Run:  uvicorn mock_prometheus:app --port 19090   (from dev/)
Then: HLIDSKJALF_METRICS_SOURCE=prometheus \
      HLIDSKJALF_PROMETHEUS_URL=http://127.0.0.1:19090 uvicorn hlidskjalf.main:app
"""

import math
import re

from fastapi import FastAPI

app = FastAPI(title="mock-prometheus")

NODE = "hella"

# vmid -> (cores, memory bytes, disk bytes, netin B/s, netout B/s), mirrors dev/mock_pve.py
GUESTS: dict[str, tuple[int, int, int, int, int]] = {
    "qemu/101": (4, 8192 << 20, 64 << 30, 1_200_000, 400_000),
    "qemu/105": (8, 16384 << 20, 200 << 30, 3_500_000, 900_000),
    "qemu/115": (2, 4096 << 20, 40 << 30, 800_000, 250_000),
    "qemu/120": (2, 4096 << 20, 32 << 30, 300_000, 120_000),
    "qemu/151": (2, 4096 << 20, 500 << 30, 90_000, 30_000),
    "lxc/130": (2, 2048 << 20, 16 << 30, 150_000, 60_000),
    "qemu/140": (1, 1024 << 20, 8 << 30, 0, 0),
}

NODE_CAPS = {"cores": 16, "memtotal": 64 << 30, "roottotal": 100 << 30}

# metric name -> value at time t, for the given id. None means "no such series".
def _wave(t: float, seed: float, lo: float, hi: float) -> float:
    return lo + (hi - lo) * abs(math.sin(t / 3000.0 + seed))


def _seed(id_: str) -> float:
    return sum(ord(c) for c in id_) % 97


def _sample(metric: str, id_: str, t: float) -> float | None:
    is_node = id_ == f"node/{NODE}"
    if not is_node and id_ not in GUESTS:
        return None
    seed = _seed(id_)

    if is_node:
        caps = NODE_CAPS
        if metric == "pve_cpu_usage_ratio":
            return _wave(t, seed, 0.08, 0.55)
        if metric == "pve_cpu_usage_limit":
            return float(caps["cores"])
        if metric == "pve_memory_usage_bytes":
            return _wave(t, seed, 0.35, 0.75) * caps["memtotal"]
        if metric == "pve_memory_size_bytes":
            return float(caps["memtotal"])
        if metric == "pve_disk_usage_bytes":
            return 0.3 * caps["roottotal"]
        if metric == "pve_disk_size_bytes":
            return float(caps["roottotal"])
        # node_exporter series, reachable only via HLIDSKJALF_PROMETHEUS_NODE_QUERIES
        if metric == "node_load1":
            return _wave(t, seed, 0.4, 4.0)
        return None

    cores, memory, disk, rin, rout = GUESTS[id_]
    if metric == "pve_cpu_usage_ratio":
        return _wave(t, seed, 0.02, 0.6)
    if metric == "pve_cpu_usage_limit":
        return float(cores)
    if metric == "pve_memory_usage_bytes":
        return _wave(t, seed, 0.3, 0.7) * memory
    if metric == "pve_memory_size_bytes":
        return float(memory)
    if metric == "pve_disk_usage_bytes":
        # containers report no disk usage here — exercises the panel's None fill
        return None if id_.startswith("lxc/") else 0.45 * disk
    if metric == "pve_disk_size_bytes":
        return float(disk)
    if metric == "pve_disk_read_bytes":  # already a rate() result, bytes/sec
        return _wave(t, seed, 10_000, 120_000)
    if metric == "pve_disk_write_bytes":
        return _wave(t, seed, 5_000, 60_000)
    if metric == "pve_network_receive_bytes":
        return _wave(t, seed, 0.4, 1.2) * rin
    if metric == "pve_network_transmit_bytes":
        return _wave(t, seed, 0.4, 1.2) * rout
    return None


METRIC_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?=\{)")
ID_RE = re.compile(r'id="([^"]+)"')
KNOWN_WRAPPERS = {"rate", "avg_over_time", "max_over_time", "sum", "irate", "increase"}


def _parse(query: str) -> tuple[str | None, str | None]:
    """(metric, id) out of a PromQL expression — wrappers ignored."""
    metric = next(
        (m for m in METRIC_RE.findall(query) if m not in KNOWN_WRAPPERS), None
    )
    id_match = ID_RE.search(query)
    return metric, (id_match.group(1) if id_match else None)


@app.get("/api/v1/query_range")
async def query_range(query: str, start: float, end: float, step: float):
    metric, id_ = _parse(query)
    if not metric or not id_ or step <= 0:
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    values = []
    t = start
    while t <= end:
        v = _sample(metric, id_, t)
        if v is not None:
            values.append([t, f"{v:.6f}"])
        t += step

    if not values:
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"__name__": metric, "id": id_, "instance": "hella:9221"},
                 "values": values}
            ],
        },
    }


@app.get("/-/healthy")
async def healthy():
    return {"status": "ok"}
