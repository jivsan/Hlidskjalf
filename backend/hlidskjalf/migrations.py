"""Ordered, versioned schema migrations with a backup before every upgrade.

Until now the schema was a pile of ``CREATE TABLE IF NOT EXISTS``: fine for adding
a table, useless for changing one, and it left no way to know what shape an
existing database was in. That is a bad place to be *before* shipping a
self-update feature — an update that silently half-migrates someone's database is
how people lose their bandwidth history.

Rules:

* Migrations are append-only. Once a version has shipped, its SQL is frozen —
  editing it would mean two databases claiming the same version with different
  shapes. Add a new one instead.
* Every migration is applied inside a transaction, in order, exactly once.
* Before applying ANY migration to an existing database, the file is copied to
  ``hlidskjalf.sqlite3.bak-v<from>-<timestamp>``. Cheap insurance; sqlite is one file.
* ``<state_dir>/secret.key`` is NOT touched by any of this — losing it orphans
  every stored secret (see secretbox.py).
"""

import logging
import shutil
import time
from pathlib import Path

import aiosqlite

log = logging.getLogger("hlidskjalf.migrations")

# (version, description, SQL). APPEND ONLY — never edit a shipped entry.
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "baseline: bandwidth, counters, rescue, switch notes, users, config",
        """
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
            boot       TEXT,
            slot       TEXT    NOT NULL,
            slot_prev  TEXT,
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
            role          TEXT NOT NULL DEFAULT 'user',
            vmid          INTEGER,
            created_at    TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vmid
            ON users(vmid) WHERE vmid IS NOT NULL;
        CREATE TABLE IF NOT EXISTS config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        "audit log + session revocation",
        """
        -- Durable record of who did what. The panel can destroy disks; doing that
        -- with no record beyond an in-memory ring buffer that dies on restart is
        -- not something you can hand to another person.
        CREATE TABLE IF NOT EXISTS audit (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT    NOT NULL,          -- UTC ISO-8601
            actor    TEXT    NOT NULL,          -- username, or '-' pre-auth
            action   TEXT    NOT NULL,          -- e.g. vm.destroy, user.delete
            target   TEXT,                      -- e.g. vmid, username
            detail   TEXT,                      -- freeform, never a secret
            client   TEXT,                      -- client IP
            ok       INTEGER NOT NULL DEFAULT 1 -- 0 = refused/failed
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor);

        -- Session cookies are stateless and signed, so a logout could not
        -- previously revoke a stolen cookie: it stayed valid until it expired.
        -- Each session now carries a random sid, and logout parks it here.
        CREATE TABLE IF NOT EXISTS revoked_sessions (
            sid        TEXT PRIMARY KEY,
            expires_at REAL NOT NULL   -- unix seconds; rows pruned after this
        );
        """,
    ),
    (
        3,
        "pangolin SSH-tunnel resources (optional integration)",
        """
        -- Maps a provisioned VM to the Pangolin TCP resource that tunnels SSH to
        -- it. One row per VM: created on provision, deleted on destroy. proxy_port
        -- is the per-VM port friends use (`ssh -p <port> user@<domain>`), drawn
        -- from a pool at/above pangolin_ssh_port_start. Only ever populated when
        -- the optional Pangolin integration is configured.
        CREATE TABLE IF NOT EXISTS pangolin_resources (
            vmid        INTEGER PRIMARY KEY,
            resource_id INTEGER NOT NULL,
            proxy_port  INTEGER NOT NULL
        );
        """,
    ),
    (
        4,
        "pangolin_resources: unique ports, NULL-able resource_id, orphan debts",
        """
        -- Security-audit rebuild of the v3 table:
        --
        -- * proxy_port gets a UNIQUE index. The old allocation read the pool
        --   and inserted only AFTER the Pangolin create returned, so two
        --   concurrent provisions could be handed the SAME port (TOCTOU). The
        --   index is the hard backstop; db.pangolin_reserve_port retries the
        --   scan on the IntegrityError.
        -- * resource_id becomes NULL-able: a row is now written as a bare port
        --   reservation BEFORE the Pangolin create, so a failure between create
        --   and record can no longer strand an untracked resource.
        -- * orphan_ids (comma-separated) carries resource ids still owed a
        --   delete. A failed Pangolin delete kept the row for retry, but a
        --   reprovision's INSERT OR REPLACE overwrote it and the orphan's id
        --   was lost forever.
        --
        -- OR IGNORE on the copy also repairs any duplicate-port rows the old
        -- race already wrote (first VMID wins; the loser's resource, if any,
        -- is now untracked — unavoidable, and the pre-migration backup holds
        -- the evidence).
        CREATE TABLE IF NOT EXISTS pangolin_resources_v4 (
            vmid        INTEGER PRIMARY KEY,
            resource_id INTEGER,
            proxy_port  INTEGER NOT NULL,
            orphan_ids  TEXT NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pangolin_resources_port
            ON pangolin_resources_v4(proxy_port);
        INSERT OR IGNORE INTO pangolin_resources_v4
            (vmid, resource_id, proxy_port, orphan_ids)
            SELECT vmid, resource_id, proxy_port, ''
            FROM pangolin_resources ORDER BY vmid;
        DROP TABLE IF EXISTS pangolin_resources;
        ALTER TABLE pangolin_resources_v4 RENAME TO pangolin_resources;
        """,
    ),
]

LATEST = max(v for v, _, _ in MIGRATIONS)


async def _current_version(conn: aiosqlite.Connection) -> int:
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    cur = await conn.execute("SELECT version FROM schema_version")
    row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def _set_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute("DELETE FROM schema_version")
    await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _backup(path: Path, from_version: int) -> Path | None:
    """Copy the database aside before we touch it. Returns the backup path."""
    if not path.is_file() or path.stat().st_size == 0:
        return None  # nothing to lose on a fresh database
    dest = path.with_name(f"{path.name}.bak-v{from_version}-{int(time.time())}")
    shutil.copy2(path, dest)
    try:
        dest.chmod(0o600)  # it contains everything the live DB does
    except OSError:  # pragma: no cover
        pass
    log.warning("backed up %s -> %s before migrating", path.name, dest.name)
    return dest


async def _has_existing_data(conn: aiosqlite.Connection) -> bool:
    """Is this a real database with someone's data in it, or a blank file?

    A database written before migrations existed (<= v0.3.6) has all the tables
    and all the data but NO schema_version row — so it reports version 0 and looks
    exactly like a fresh install. Backing up on version alone would skip precisely
    the databases that most need it.
    """
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('users','bandwidth') LIMIT 1"
    )
    return await cur.fetchone() is not None


async def migrate(conn: aiosqlite.Connection, path: Path) -> int:
    """Bring the database up to LATEST. Returns the version now in force."""
    current = await _current_version(conn)

    if current > LATEST:
        raise RuntimeError(
            f"Database schema is v{current} but this build only knows up to v{LATEST}. "
            "You are running an OLDER Hlidskjalf against a NEWER database — refusing "
            "to touch it. Restore a backup or upgrade the panel."
        )

    pending = [(v, d, sql) for v, d, sql in MIGRATIONS if v > current]
    if not pending:
        return current

    if await _has_existing_data(conn):
        _backup(path, current)

    for version, description, sql in pending:
        log.info("applying schema migration v%d: %s", version, description)
        await conn.executescript(sql)
        await _set_version(conn, version)
        await conn.commit()

    log.info("database schema is now v%d", LATEST)
    return LATEST
