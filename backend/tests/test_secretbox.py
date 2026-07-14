"""Secrets at rest.

The guarantee: the Proxmox API token is NEVER written to the state database in
plaintext. These tests read the raw sqlite file off disk and grep it — if the
token ever appears there, they fail.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from hlidskjalf import secretbox
from hlidskjalf.config import FILE_BACKED, Settings, seal, unseal

TOKEN = "s3cr3t-proxmox-token-3f9a1c00-do-not-leak"


@pytest.fixture
def state_dir():
    return tempfile.mkdtemp(prefix="hlidskjalf-secretbox-")


# --- round trip --------------------------------------------------------------


def test_encrypt_then_decrypt_round_trips():
    key = "a-perfectly-good-key"
    sealed = secretbox.encrypt(TOKEN, key)
    assert sealed != TOKEN
    assert secretbox.is_encrypted(sealed)
    assert TOKEN not in sealed
    assert secretbox.decrypt(sealed, key) == TOKEN


def test_ciphertext_differs_each_time():
    """Fernet is randomised — two encryptions of the same token must not match,
    or equal tokens would be identifiable by their ciphertext."""
    key = "a-perfectly-good-key"
    assert secretbox.encrypt(TOKEN, key) != secretbox.encrypt(TOKEN, key)


def test_a_real_fernet_key_is_accepted_as_is():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    assert secretbox.decrypt(secretbox.encrypt(TOKEN, key), key) == TOKEN


def test_wrong_key_is_refused_not_garbage():
    sealed = secretbox.encrypt(TOKEN, "the-right-key")
    with pytest.raises(secretbox.SecretBoxError, match="does not decrypt"):
        secretbox.decrypt(sealed, "the-wrong-key")


def test_missing_key_for_encrypted_value_is_a_clear_error():
    sealed = secretbox.encrypt(TOKEN, "some-key")
    with pytest.raises(secretbox.SecretBoxError, match="HLIDSKJALF_SECRET_KEY"):
        secretbox.decrypt(sealed, "")


def test_plaintext_values_pass_through():
    """Config written before encryption existed must still load."""
    assert secretbox.decrypt("legacy-plaintext", "a-key") == "legacy-plaintext"


# --- only secrets are encrypted ----------------------------------------------


def test_only_secret_fields_are_sealed():
    cfg = {"pve_host": "192.168.0.1", "pve_node": "pve", "pve_token_secret": TOKEN}
    sealed = secretbox.encrypt_config(cfg, "a-key")
    assert sealed["pve_host"] == "192.168.0.1"  # not a secret, stays readable
    assert sealed["pve_node"] == "pve"
    assert secretbox.is_encrypted(sealed["pve_token_secret"])
    assert secretbox.decrypt_config(sealed, "a-key") == cfg


def test_the_session_key_is_a_secret_too():
    assert "session_secret" in secretbox.SECRET_KEYS
    assert "pve_token_secret" in secretbox.SECRET_KEYS


# --- key resolution ----------------------------------------------------------


def test_generates_a_0600_key_file_when_none_supplied(state_dir):
    key = secretbox.resolve_key("", state_dir)
    path = Path(state_dir) / secretbox.KEY_FILENAME
    assert path.is_file()
    assert key
    assert oct(path.stat().st_mode)[-3:] == "600"  # not readable by anyone else


def test_the_generated_key_is_stable_across_restarts(state_dir):
    first = secretbox.resolve_key("", state_dir)
    second = secretbox.resolve_key("", state_dir)
    assert first == second  # a regenerated key would orphan every stored secret


def test_an_explicit_key_wins_and_writes_no_key_file(state_dir):
    """The externally-managed key is the mode that survives a stolen disk — it
    must not leave a copy of itself lying next to the database."""
    assert secretbox.resolve_key("key-from-systemd-creds", state_dir) == "key-from-systemd-creds"
    assert not (Path(state_dir) / secretbox.KEY_FILENAME).exists()


# --- the actual guarantee: nothing plaintext hits the disk --------------------


@pytest.mark.asyncio
async def test_the_token_never_appears_in_the_database_file(state_dir):
    from hlidskjalf.db import Db

    settings = Settings(state_dir=state_dir)
    db = Db(Path(state_dir) / "hlidskjalf.sqlite3")
    await db.open()
    await db.set_config(seal({"pve_host": "192.168.0.1", "pve_token_secret": TOKEN}, settings))
    await db.close()

    # Read the raw file, exactly as someone who walked off with it would.
    blob = (Path(state_dir) / "hlidskjalf.sqlite3").read_bytes()
    assert TOKEN.encode() not in blob, "the Proxmox token is sitting in the DB in plaintext"

    # And via sqlite, in case of page-boundary luck above.
    conn = sqlite3.connect(Path(state_dir) / "hlidskjalf.sqlite3")
    rows = dict(conn.execute("SELECT key, value FROM config").fetchall())
    conn.close()
    assert rows["pve_token_secret"] != TOKEN
    assert secretbox.is_encrypted(rows["pve_token_secret"])
    assert rows["pve_host"] == "192.168.0.1"  # non-secrets stay legible for debugging

    # The panel itself still gets the real token back.
    assert unseal(rows, settings)["pve_token_secret"] == TOKEN


# --- *_FILE indirection ------------------------------------------------------


def test_secrets_can_be_supplied_as_files(monkeypatch, state_dir):
    """systemd LoadCredential / Docker / k8s secrets hand you a FILE, not an env
    var — and an env var leaks into /proc, logs and crash dumps."""
    p = Path(state_dir) / "token"
    p.write_text(TOKEN + "\n")  # trailing newline is normal for mounted secrets
    monkeypatch.setenv("HLIDSKJALF_PVE_TOKEN_SECRET_FILE", str(p))

    from hlidskjalf import config

    config.get_settings.cache_clear()
    try:
        s = config.get_settings()
        assert s.pve_token_secret == TOKEN  # newline stripped
        # Must count as env-provided, so stored config can never override it.
        assert "pve_token_secret" in s.model_fields_set
    finally:
        config.get_settings.cache_clear()


def test_a_missing_secret_file_fails_loudly(monkeypatch):
    monkeypatch.setenv("HLIDSKJALF_PVE_TOKEN_SECRET_FILE", "/nope/not/here")
    from hlidskjalf import config

    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="does not exist"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_every_secret_is_file_backed():
    """A secret you cannot supply as a file cannot be managed by a secret store."""
    for field in secretbox.SECRET_KEYS:
        assert field in FILE_BACKED, f"{field} should be settable via *_FILE"
