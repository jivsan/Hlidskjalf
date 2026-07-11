"""MetricsSource protocol — rrddata today, Prometheus in phase 2."""

from typing import Protocol

Timeframe = str  # hour | day | week | month | year


class MetricsSource(Protocol):
    async def get_vm_series(self, vmid: int, kind: str, timeframe: Timeframe, cf: str) -> list[dict]:
        """Rows shaped {t, cpu, maxcpu, mem, maxmem, disk, maxdisk, diskread, diskwrite, netin, netout}."""
        ...

    async def get_node_series(self, timeframe: Timeframe, cf: str) -> list[dict]:
        ...
