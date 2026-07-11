"""Accumulator unit tests: no servers, a fake PveClient with canned resources.

`Accumulator.start()` also spawns the 60 s loop task; these tests instead load
baselines the same way start() does (`prev = db.load_counters()`) and drive
`sample_once()` by hand, so nothing races.
"""

import pytest

from hlidskjalf.accumulator import Accumulator
from hlidskjalf.db import Db, today_utc


class FakePve:
    def __init__(self):
        self.resources: list[dict] = []

    async def cluster_resources(self, type_: str | None = "vm") -> list[dict]:
        return self.resources


def running(vmid, netin, netout, **extra):
    return {"vmid": vmid, "status": "running", "netin": netin, "netout": netout, **extra}


@pytest.fixture()
async def db(tmp_path):
    d = Db(tmp_path / "test.sqlite3")
    await d.open()
    yield d
    await d.close()


async def _totals(db: Db, vmid: int) -> tuple[int, int]:
    rows = await db.bandwidth_range(vmid, "0000-01-01", "9999-12-31")
    return sum(r["bytes_in"] for r in rows), sum(r["bytes_out"] for r in rows)


async def test_first_sample_books_nothing(db):
    pve = FakePve()
    pve.resources = [running(500, 1_000_000, 400_000)]
    acc = Accumulator(pve, db)
    acc.prev = await db.load_counters()

    await acc.sample_once()
    assert await _totals(db, 500) == (0, 0)
    # ... but the baseline was established and persisted
    assert (await db.load_counters())[500] == (1_000_000, 400_000)


async def test_steady_counters_book_deltas(db):
    pve = FakePve()
    acc = Accumulator(pve, db)
    acc.prev = await db.load_counters()

    pve.resources = [running(500, 1_000, 400)]
    await acc.sample_once()
    pve.resources = [running(500, 1_500, 700)]
    await acc.sample_once()
    pve.resources = [running(500, 2_500, 1_200)]
    await acc.sample_once()

    assert await _totals(db, 500) == (1_500, 800)
    rows = await db.bandwidth_range(500, today_utc(), today_utc())
    assert rows == [{"date": today_utc(), "bytes_in": 1_500, "bytes_out": 800}]


async def test_counter_reset_books_current_value(db):
    pve = FakePve()
    acc = Accumulator(pve, db)
    acc.prev = await db.load_counters()

    pve.resources = [running(500, 10_000, 5_000)]
    await acc.sample_once()
    # VM restarted: counters began again from zero, everything since counts
    pve.resources = [running(500, 300, 120)]
    await acc.sample_once()

    assert await _totals(db, 500) == (300, 120)
    assert (await db.load_counters())[500] == (300, 120)


async def test_baselines_survive_restart_without_double_counting(db):
    pve = FakePve()
    acc1 = Accumulator(pve, db)
    acc1.prev = await db.load_counters()

    pve.resources = [running(500, 1_000, 400)]
    await acc1.sample_once()
    pve.resources = [running(500, 2_000, 900)]
    await acc1.sample_once()
    assert await _totals(db, 500) == (1_000, 500)

    # simulated panel restart: fresh Accumulator over the same Db
    acc2 = Accumulator(pve, db)
    acc2.prev = await db.load_counters()
    assert acc2.prev[500] == (2_000, 900)

    # same counters again — nothing new happened, nothing gets double-counted
    await acc2.sample_once()
    assert await _totals(db, 500) == (1_000, 500)

    # traffic since the restart books normally
    pve.resources = [running(500, 2_600, 1_000)]
    await acc2.sample_once()
    assert await _totals(db, 500) == (1_600, 600)


async def test_stopped_vms_create_no_baseline(db):
    pve = FakePve()
    acc = Accumulator(pve, db)
    acc.prev = await db.load_counters()

    pve.resources = [
        {"vmid": 501, "status": "stopped", "netin": 0, "netout": 0},
        running(500, 1_000, 400),
    ]
    await acc.sample_once()

    assert 501 not in acc.prev
    assert 501 not in await db.load_counters()
    assert 500 in acc.prev


async def test_templates_are_skipped(db):
    pve = FakePve()
    acc = Accumulator(pve, db)
    acc.prev = await db.load_counters()

    pve.resources = [running(9000, 1_000, 400, template=1)]
    await acc.sample_once()
    pve.resources = [running(9000, 9_000, 4_000, template=1)]
    await acc.sample_once()

    assert 9000 not in acc.prev
    assert await _totals(db, 9000) == (0, 0)
