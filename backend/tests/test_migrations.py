"""Schema migrations and the backup that guards them.

The schema used to be a pile of CREATE TABLE IF NOT EXISTS: fine for adding a
table, useless for changing one, and it gave no way to know what shape an existing
database was in. That is not a state to be in *before* shipping self-update.
"""

import sqlite3
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from hlidskjalf import migrations
from hlidskjalf.db import Db


@pytest.fixture
def tmpdb():
    return Path(tempfile.mkdtemp(prefix="hlidskjalf-migr-")) / "hlidskjalf.sqlite3"


@pytest.mark.asyncio
async def test_a_fresh_database_lands_on_the_latest_version(tmpdb):
    db = Db(tmpdb)
    await db.open()
    assert db.schema_version == migrations.LATEST
    await db.close()

    conn = sqlite3.connect(tmpdb)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"users", "bandwidth", "config", "audit", "revoked_sessions"} <= tables


@pytest.mark.asyncio
async def test_migrating_is_idempotent(tmpdb):
    db = Db(tmpdb)
    await db.open()
    await db.close()

    db2 = Db(tmpdb)
    await db2.open()  # must not re-run anything or explode
    assert db2.schema_version == migrations.LATEST
    await db2.close()


@pytest.mark.asyncio
async def test_a_fresh_database_is_not_pointlessly_backed_up(tmpdb):
    db = Db(tmpdb)
    await db.open()
    await db.close()
    assert not list(tmpdb.parent.glob("*.bak-*")), "backed up an empty database"


@pytest.mark.asyncio
async def test_a_pre_migration_database_is_backed_up_before_being_touched(tmpdb):
    """A database written before migrations existed has all the data but NO
    schema_version row — so it reports version 0 and looks exactly like a fresh
    install. Backing up on version alone would skip precisely the databases that
    most need it."""
    # Forge a legacy DB: real tables, real data, no schema_version.
    conn = sqlite3.connect(tmpdb)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                            password_hash TEXT NOT NULL, role TEXT, vmid INTEGER,
                            created_at TEXT NOT NULL);
        CREATE TABLE bandwidth (vmid INTEGER, date TEXT, bytes_in INTEGER,
                                bytes_out INTEGER, PRIMARY KEY (vmid, date));
        INSERT INTO users VALUES (1, 'legacy-admin', 'hash', 'admin', NULL, 'then');
        INSERT INTO bandwidth VALUES (105, '2026-07-01', 999, 111);
        """
    )
    conn.commit()
    conn.close()

    db = Db(tmpdb)
    await db.open()
    assert db.schema_version == migrations.LATEST
    await db.close()

    backups = list(tmpdb.parent.glob("*.bak-*"))
    assert backups, "an existing database was migrated with no backup taken"

    # The backup still holds the original data...
    old = sqlite3.connect(backups[0])
    assert old.execute("SELECT count(*) FROM bandwidth").fetchone()[0] == 1
    old.close()

    # ...and the live database kept it too, plus the new tables.
    new = sqlite3.connect(tmpdb)
    assert new.execute("SELECT username FROM users").fetchone()[0] == "legacy-admin"
    assert new.execute("SELECT bytes_in FROM bandwidth").fetchone()[0] == 999
    new.execute("SELECT 1 FROM audit LIMIT 1")  # new table exists
    new.close()


@pytest.mark.asyncio
async def test_an_older_build_refuses_a_newer_database(tmpdb):
    """Running an old panel against a database a newer one has already migrated
    would silently corrupt it. Refuse instead."""
    conn = await aiosqlite.connect(tmpdb)
    await migrations.migrate(conn, tmpdb)
    await conn.execute("DELETE FROM schema_version")
    await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (migrations.LATEST + 5,))
    await conn.commit()

    with pytest.raises(RuntimeError, match="older Hlidskjalf against a NEWER database|only knows up to"):
        await migrations.migrate(conn, tmpdb)
    await conn.close()


def test_migrations_are_ordered_and_unique():
    versions = [v for v, _, _ in migrations.MIGRATIONS]
    assert versions == sorted(versions), "migrations must be in ascending order"
    assert len(versions) == len(set(versions)), "two migrations claim the same version"
    assert versions[0] == 1
