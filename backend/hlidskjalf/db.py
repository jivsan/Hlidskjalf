"""SQLite state: bandwidth accounting, counter baselines, rescue boot-order stash,
users, and (after the first-run setup wizard) runtime configuration."""

import asyncio
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
        # Serializes pangolin port reservation (scan + insert must be one
        # critical section — see pangolin_reserve_port).
        self._pangolin_lock = asyncio.Lock()

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

    # --- pangolin SSH-tunnel resources (optional integration) ------------------
    # One row per VM (schema v4). resource_id is the LIVE Pangolin resource id —
    # NULL while the create has not been recorded yet (a bare port reservation)
    # or is already torn down. orphan_ids is a comma-separated list of resource
    # ids still owed a delete: a destroy whose Pangolin delete failed keeps them
    # here, and a reprovision carries a previous live id into the list, so an
    # orphan is never silently forgotten (INSERT OR REPLACE used to do exactly
    # that). proxy_port is UNIQUE: two VMs must never share a tunnel port.

    async def pangolin_reserve_port(self, vmid: int, start: int) -> int:
        """Atomically reserve a proxy port for `vmid` and return it.

        The scan of the used set and the insert of the reservation row are one
        critical section — the per-Db lock serializes the panel's own requests,
        and the UNIQUE index on proxy_port is the backstop that turns any
        residual race into an IntegrityError, retried with the next free port.
        The old code read the pool and inserted only AFTER the Pangolin create
        returned; two concurrent provisions could be handed the same port.

        Reprovisioning a vmid that still has a row keeps its port and carries a
        live resource_id into orphan_ids (see the note above).
        """
        async with self._pangolin_lock:
            for _ in range(10):
                try:
                    cur = await self.conn.execute(
                        "SELECT resource_id, proxy_port, orphan_ids "
                        "FROM pangolin_resources WHERE vmid = ?",
                        (vmid,),
                    )
                    row = await cur.fetchone()
                    if row is not None:
                        orphans = row["orphan_ids"] or ""
                        if row["resource_id"] is not None:
                            orphans = (
                                f"{orphans},{row['resource_id']}"
                                if orphans
                                else str(row["resource_id"])
                            )
                        await self.conn.execute(
                            "UPDATE pangolin_resources "
                            "SET resource_id = NULL, orphan_ids = ? WHERE vmid = ?",
                            (orphans, vmid),
                        )
                        await self.conn.commit()
                        return int(row["proxy_port"])
                    cur = await self.conn.execute(
                        "SELECT proxy_port FROM pangolin_resources"
                    )
                    used = {r["proxy_port"] for r in await cur.fetchall()}
                    port = start
                    while port in used:
                        port += 1
                    await self.conn.execute(
                        "INSERT INTO pangolin_resources "
                        "(vmid, resource_id, proxy_port, orphan_ids) "
                        "VALUES (?, NULL, ?, '')",
                        (vmid, port),
                    )
                    await self.conn.commit()
                    return port
                except aiosqlite.IntegrityError:
                    # The UNIQUE(proxy_port) backstop fired: another writer took
                    # that port between our scan and our insert. Scan again.
                    continue
        raise RuntimeError("could not reserve a Pangolin proxy port")

    async def pangolin_set_resource(self, vmid: int, resource_id: int) -> None:
        """Record the created resource id on the reservation row. Called
        IMMEDIATELY after the Pangolin create returns — before any further
        fallible step — so a later failure can never strand an untracked
        resource."""
        await self.conn.execute(
            "UPDATE pangolin_resources SET resource_id = ? WHERE vmid = ?",
            (resource_id, vmid),
        )
        await self.conn.commit()

    async def pangolin_release_port(self, vmid: int) -> None:
        """The Pangolin create failed before anything existed over there: hand
        the port back. A row that still carries orphan debts is KEPT (with a
        NULL resource_id) — those ids must survive for a later delete retry."""
        await self.conn.execute(
            "DELETE FROM pangolin_resources "
            "WHERE vmid = ? AND resource_id IS NULL AND orphan_ids = ''",
            (vmid,),
        )
        await self.conn.commit()

    async def pangolin_get(self, vmid: int) -> dict | None:
        cur = await self.conn.execute(
            "SELECT vmid, resource_id, proxy_port, orphan_ids "
            "FROM pangolin_resources WHERE vmid = ?",
            (vmid,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["orphan_ids"] = [
            int(x) for x in (d["orphan_ids"] or "").split(",") if x.strip()
        ]
        return d

    async def pangolin_set_debts(self, vmid: int, orphan_ids: list[int]) -> None:
        """After a destroy: the VM's live resource is gone one way or another
        (resource_id becomes NULL) and whatever deletes FAILED stay on the row
        as orphan debts for a later retry."""
        await self.conn.execute(
            "UPDATE pangolin_resources SET resource_id = NULL, orphan_ids = ? "
            "WHERE vmid = ?",
            (",".join(str(i) for i in orphan_ids), vmid),
        )
        await self.conn.commit()

    async def pangolin_delete(self, vmid: int) -> None:
        await self.conn.execute("DELETE FROM pangolin_resources WHERE vmid = ?", (vmid,))
        await self.conn.commit()

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

    async def create_user(self, username: str, password_hash: str, role: str = "user", vmid: int | None = None, email: str = "") -> int:
        if role not in ("admin", "user"):
            role = "user"
        now = datetime.now(timezone.utc).isoformat()
        cur = await self.conn.execute(
            "INSERT INTO users (username, password_hash, role, vmid, created_at, email) VALUES (?, ?, ?, ?, ?, ?)",
            (username, password_hash, role, vmid, now, email.strip().lower()),
        )
        await self.conn.commit()
        return cur.lastrowid  # type: ignore

    async def get_user_by_username(self, username: str) -> dict | None:
        cur = await self.conn.execute(
            "SELECT id, username, password_hash, role, vmid, created_at, email, pangolin_state, pangolin_invite_id FROM users WHERE username = ?",
            (username,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, uid: int) -> dict | None:
        cur = await self.conn.execute(
            "SELECT id, username, password_hash, role, vmid, created_at, email, pangolin_state, pangolin_invite_id FROM users WHERE id = ?",
            (uid,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_users(self) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT id, username, role, vmid, created_at, email, pangolin_state FROM users ORDER BY role DESC, username"
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

    async def set_user_pangolin_state(self, username: str, state: str, invite_id: str = "") -> None:
        """Track the edge-identity sync: '', 'invited', 'active', 'error'.
        The invite LINK is never stored — only the invitation's id, so an
        unaccepted invite can be cancelled on user delete."""
        await self.conn.execute(
            "UPDATE users SET pangolin_state = ?, pangolin_invite_id = ? WHERE username = ?",
            (state, invite_id, username),
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
