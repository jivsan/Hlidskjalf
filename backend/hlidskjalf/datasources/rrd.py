"""RRDSource — shapes PVE rrddata into the panel's series rows."""

from ..pve import PveClient

VM_FIELDS = ("cpu", "maxcpu", "mem", "maxmem", "disk", "maxdisk",
             "diskread", "diskwrite", "netin", "netout")
NODE_FIELDS = ("cpu", "maxcpu", "memused", "memtotal", "iowait",
               "netin", "netout", "loadavg", "rootused", "roottotal")


def _shape(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    out = []
    for r in rows:
        row = {"t": r.get("time")}
        for f in fields:
            v = r.get(f)
            row[f] = float(v) if v is not None else None
        out.append(row)
    return out


class RRDSource:
    def __init__(self, pve: PveClient):
        self.pve = pve

    async def get_vm_series(self, vmid: int, kind: str, timeframe: str, cf: str) -> list[dict]:
        rows = await self.pve.get(
            f"/nodes/{self.pve.node}/{kind}/{vmid}/rrddata",
            timeframe=timeframe, cf=cf,
        )
        return _shape(rows or [], VM_FIELDS)

    async def get_node_series(self, timeframe: str, cf: str) -> list[dict]:
        rows = await self.pve.get(
            f"/nodes/{self.pve.node}/rrddata", timeframe=timeframe, cf=cf
        )
        return _shape(rows or [], NODE_FIELDS)
