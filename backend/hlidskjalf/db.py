"""SQLite state: bandwidth accounting, counter baselines, rescue boot-order stash."""

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS bandwidth (
    vmid      INTEGER NOT NULL,
    date      TEXT    NOT NULL,          -- UTC YYYY-MM-DD
    bytes_in  INTEGER NOT NULL DEFAULT 0,
    bytes_out INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (vmid, date)
);
CREATE TABLE IF NOT EXISTS counters (
    vmid       INTEGER PRIMARY KEY,
    netin      INTEGER NOT NULL,
    netout     INTEGER NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS rescue (
    vmid       INTEGER PRIMARY KEY,
    boot       TEXT,                     -- original boot order value ('' = unset)
    slot       TEXT    NOT NULL,         -- ide slot used for the rescue ISO
    slot_prev  TEXT,                     -- original value of that slot ('' = unset)
    entered_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS switch_port_notes (
    name       TEXT PRIMARY KEY,
    note       TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    vmid          INTEGER,                        -- assigned VM for regular users (unique)
    created_at    TEXT NOT NULL
);

-- ensure at most one user per vmid (for non-admin users)
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vmid ON users(vmid) WHERE vmid IS NOT NULL;
"""


class Db:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "db not opened"
        return self._conn

    # --- bandwidth ------------------------------------------------------------

    async def add_bandwidth(self, vmid: int, day: str, d_in: int, d_out: int) -> None:
        await self.conn.execute(
            """INSERT INTO bandwidth (vmid, date, bytes_in, bytes_out)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (vmid, date) DO UPDATE SET
                 bytes_in = bytes_in + excluded.bytes_in,
                 bytes_out = bytes_out + excluded.bytes_out""",
            (vmid, day, d_in, d_out),
        )

    async def bandwidth_range(self, vmid: int, from_: str, to: str) -> list[dict]:
        cur = await self.conn.execute(
            """SELECT date, bytes_in, bytes_out FROM bandwidth
               WHERE vmid = ? AND date >= ? AND date <= ? ORDER BY date""",
            (vmid, from_, to),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def bandwidth_monthly(self, vmid: int, year: int) -> list[dict]:
        cur = await self.conn.execute(
            """SELECT substr(date, 1, 7) AS month,
                      SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out
               FROM bandwidth
               WHERE vmid = ? AND date >= ? AND date <= ?
               GROUP BY month ORDER BY month""",
            (vmid, f"{year}-01-01", f"{year}-12-31"),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def bandwidth_summary(self, month: str) -> list[dict]:
        """Per-VM totals for a YYYY-MM month, across all VMs."""
        cur = await self.conn.execute(
            """SELECT vmid, SUM(bytes_in) AS bytes_in, SUM(bytes_out) AS bytes_out
               FROM bandwidth WHERE substr(date, 1, 7) = ?
               GROUP BY vmid""",
            (month,),
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- counter baselines ----------------------------------------------------

    async def load_counters(self) -> dict[int, tuple[int, int]]:
        cur = await self.conn.execute("SELECT vmid, netin, netout FROM counters")
        return {r["vmid"]: (r["netin"], r["netout"]) for r in await cur.fetchall()}

    async def save_counter(self, vmid: int, netin: int, netout: int) -> None:
        await self.conn.execute(
            """INSERT INTO counters (vmid, netin, netout, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (vmid) DO UPDATE SET
                 netin = excluded.netin, netout = excluded.netout,
                 updated_at = excluded.updated_at""",
            (vmid, netin, netout, datetime.now(timezone.utc).isoformat()),
        )

    async def commit(self) -> None:
        await self.conn.commit()

    # --- rescue stash -----------------------------------------------------------

    async def rescue_get(self, vmid: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM rescue WHERE vmid = ?", (vmid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def rescue_set(self, vmid: int, boot: str, slot: str, slot_prev: str) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO rescue (vmid, boot, slot, slot_prev, entered_at)
               VALUES (?, ?, ?, ?, ?)""",
            (vmid, boot, slot, slot_prev, datetime.now(timezone.utc).isoformat()),
        )
        await self.conn.commit()

    async def rescue_clear(self, vmid: int) -> None:
        await self.conn.execute("DELETE FROM rescue WHERE vmid = ?", (vmid,))
        await self.conn.commit()

    async def rescue_all(self) -> list[int]:
        cur = await self.conn.execute("SELECT vmid FROM rescue")
        return [r["vmid"] for r in await cur.fetchall()]

    # --- switch port notes ----------------------------------------------------

    async def set_port_note(self, name: str, note: str) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO switch_port_notes (name, note, updated_at)
               VALUES (?, ?, ?)""",
            (name, note, datetime.now(timezone.utc).isoformat()),
        )
        await self.conn.commit()

    async def get_port_notes(self) -> dict[str, str]:
        cur = await self.conn.execute("SELECT name, note FROM switch_port_notes")
        return {r["name"]: r["note"] for r in await cur.fetchall()}

    # --- users (multi-user + roles) -------------------------------------------

    async def create_user(self, username: str, password_hash: str, role: str = "user", vmid: int | None = None) -> int:
        if role not in ("admin", "user"):
            role = "user"
        now = datetime.now(timezone.utc).isoformat()
        cur = await self.conn.execute(
            "INSERT INTO users (username, password_hash, role, vmid, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, vmid, now),
        )
        await self.conn.commit()
        return cur.lastrowid  # type: ignore

    async def get_user_by_username(self, username: str) -> dict | None:
        cur = await self.conn.execute(
            "SELECT id, username, password_hash, role, vmid, created_at FROM users WHERE username = ?",
            (username,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, uid: int) -> dict | None:
        cur = await self.conn.execute(
            "SELECT id, username, password_hash, role, vmid, created_at FROM users WHERE id = ?",
            (uid,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_users(self) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT id, username, role, vmid, created_at FROM users ORDER BY role DESC, username"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_user_password(self, username: str, new_hash: str) -> None:
        await self.conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (new_hash, username),
        )
        await self.conn.commit()

    async def update_user_vmid(self, username: str, vmid: int | None) -> None:
        await self.conn.execute(
            "UPDATE users SET vmid = ? WHERE username = ?",
            (vmid, username),
        )
        await self.conn.commit()

    async def delete_user(self, username: str) -> None:
        await self.conn.execute("DELETE FROM users WHERE username = ?", (username,))
        await self.conn.commit()

    async def ensure_bootstrap_admin(self, username: str, password_hash: str) -> None:
        """If no users at all, create the initial admin from env (dev / first-run convenience)."""
        cur = await self.conn.execute("SELECT COUNT(*) as c FROM users")
        row = await cur.fetchone()
        if row and row["c"] == 0 and username and password_hash:
            now = datetime.now(timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO users (username, password_hash, role, vmid, created_at) VALUES (?, ?, 'admin', NULL, ?)",
                (username, password_hash, now),
            )
            await self.conn.commit()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()
