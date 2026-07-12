"""Per-VM bandwidth accounting.

PVE only exposes live cumulative netin/netout byte counters (reset on VM
stop/start), so the panel does its own bookkeeping: every 60 s, read the
counters for every guest from a single /cluster/resources call, compute the
delta against the previous sample, and add it to today's UTC row in sqlite.

Counter-reset rule: if the current value is *lower* than the previous one the
guest restarted and the counter began again at zero — everything since restart
counts, so the delta is the current value itself.

Baselines are persisted every cycle so a panel restart neither double-counts
nor loses the window between last persist and the restart.
"""

import asyncio
import logging

from .db import Db, today_utc
from .pve import PveClient, PveError

log = logging.getLogger("hlidskjalf.accumulator")

INTERVAL = 60.0


class Accumulator:
    def __init__(self, pve: PveClient, db: Db):
        self.pve = pve
        self.db = db
        self.prev: dict[int, tuple[int, int]] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.prev = await self.db.load_counters()
        log.info("loaded %d persisted counter baselines", len(self.prev))
        self._task = asyncio.create_task(self._loop(), name="bandwidth-accumulator")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.sample_once()
            except PveError as e:
                log.warning("sample skipped: %s", e)
            except Exception:
                log.exception("accumulator cycle failed")
            await asyncio.sleep(INTERVAL)

    async def sample_once(self) -> None:
        resources = await self.pve.cluster_resources()
        day = today_utc()
        for r in resources:
            vmid = r.get("vmid")
            if vmid is None or r.get("template") == 1:
                continue
            cur_in, cur_out = int(r.get("netin") or 0), int(r.get("netout") or 0)
            if r.get("status") != "running" and vmid not in self.prev:
                continue
            prev = self.prev.get(vmid)
            if prev is not None:
                # counter-reset rule: counter restarted from 0 → count it all
                d_in = cur_in - prev[0] if cur_in >= prev[0] else cur_in
                d_out = cur_out - prev[1] if cur_out >= prev[1] else cur_out
                if d_in or d_out:
                    await self.db.add_bandwidth(vmid, day, d_in, d_out)
            # first sample after start has no baseline — establish one, count nothing
            self.prev[vmid] = (cur_in, cur_out)
            await self.db.save_counter(vmid, cur_in, cur_out)
        await self.db.commit()

    def get_status(self) -> dict:
        """Return lightweight status for /api/debug/accumulator."""
        return {
            "running": self._task is not None and not self._task.done(),
            "prev_count": len(self.prev),
            "task_name": getattr(self._task, "name", None) if self._task else None,
        }

    def get_status(self) -> dict:
        """Return simple status for /api/debug/accumulator."""
        task = self._task
        running = bool(task and not task.done())
        return {
            "running": running,
            "prev_count": len(self.prev),
            "task_name": getattr(task, "get_name", lambda: None)() if task else None,
        }
