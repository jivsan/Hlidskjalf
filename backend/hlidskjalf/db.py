"""SQLite state: bandwidth accounting, counter baselines, rescue boot-order stash,
users, and (after the first-run setup wizard) runtime configuration."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .migrations import migrate

log = logging.getLogger("hlidskjalf.db")

# NOTE: the schema now lives in migrations.py (v1 is the baseline). Adding a
# table means appending a migration, not editing a blob here.


class Db:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        # Versioned, ordered migrations — and a backup of any database that
        # already holds data before we touch its shape. See migrations.py.
        self.schema_version = await migrate(self._conn, self.path)
        await self._conn.commit()
        # The DB holds password hashes and (after the setup wizard) the Proxmox
        # token secret, so it must not be world/group readable.
        try:
            self.path.chmod(0o600)
            self.path.parent.chmod(0o700)
        except OSError:  # pragma: no cover — exotic filesystems
            log.warning("could not tighten permissions on %s", self.path)

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

    # --- runtime config (written by the setup wizard) --------------------------

    async def get_config(self) -> dict[str, str]:
        cur = await self.conn.execute("SELECT key, value FROM config")
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    async def set_config(self, values: dict[str, str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.executemany(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(k, v, now) for k, v in values.items()],
        )
        await self.conn.commit()

    # --- audit log ------------------------------------------------------------

    async def audit(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        detail: str | None = None,
        client: str | None = None,
        ok: bool = True,
    ) -> None:
        """Record who did what. Never pass a secret in `detail`."""
        await self.conn.execute(
            "INSERT INTO audit (ts, actor, action, target, detail, client, ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor or "-",
                action,
                target,
                detail,
                client,
                1 if ok else 0,
            ),
        )
        await self.conn.commit()

    async def audit_recent(
        self, limit: int = 200, actor: str | None = None, action: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM audit"
        where, args = [], []
        if actor:
            where.append("actor = ?")
            args.append(actor)
        if action:
            where.append("action LIKE ?")
            args.append(f"{action}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        cur = await self.conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def audit_prune(self, keep: int = 20000) -> None:
        """Cap the log so it cannot grow without bound on a busy panel."""
        await self.conn.execute(
            "DELETE FROM audit WHERE id <= "
            "(SELECT MAX(id) FROM audit) - ?",
            (keep,),
        )
        await self.conn.commit()

    # --- session revocation ---------------------------------------------------

    async def revoke_session(self, sid: str, expires_at: float) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO revoked_sessions (sid, expires_at) VALUES (?, ?)",
            (sid, expires_at),
        )
        await self.conn.commit()

    async def is_session_revoked(self, sid: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM revoked_sessions WHERE sid = ? LIMIT 1", (sid,)
        )
        return await cur.fetchone() is not None

    async def prune_revoked_sessions(self, now: float) -> None:
        """A revoked session only needs remembering until it would have expired."""
        await self.conn.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (now,))
        await self.conn.commit()

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
