"""Encryption at rest for the secrets the panel stores (Proxmox API token, the
session signing key).

**The token is never written to the database in plaintext.** Encryption is always
on; the only question is where the key comes from, and that determines what you
are actually protected against. Be clear-eyed about this:

1. **External key** — ``HLIDSKJALF_SECRET_KEY`` / ``HLIDSKJALF_SECRET_KEY_FILE``,
   fed by systemd ``LoadCredential=``/``systemd-creds``, a Docker/Kubernetes secret,
   or a KMS. The key never rests on the panel's disk.
   *Protects against:* a stolen disk image, a leaked backup or volume snapshot, and
   anyone who walks off with the state directory. This is the mode to want.

2. **Generated key file** (the default) — ``<state_dir>/secret.key``, 0600, created
   on first run and kept in a **separate file from the database**.
   *Protects against:* the realistic accident — someone copies, backs up, `scp`s or
   attaches the `.sqlite3` on its own. That is how these things usually leak.
   *Does NOT protect against:* an attacker who can already read the state directory
   as the service user or as root. They can read the key too. Encrypting under a key
   that sits next to the ciphertext does not stop local root, and this module will
   not pretend otherwise.

There is no plaintext mode. If you want the secret out of the panel's storage
entirely, set ``HLIDSKJALF_PVE_TOKEN_SECRET`` (or its ``_FILE`` form) in the
environment — env always wins and nothing is persisted.
"""

import base64
import hashlib
import logging
import os
import secrets as _secrets
from pathlib import Path

log = logging.getLogger("hlidskjalf.secretbox")

# Stored values carry this prefix so we can tell ciphertext from a value that was
# written before encryption was switched on (and migrate it on next write).
PREFIX = "enc:v1:"

# Settings whose stored value is a secret. Anything listed here is encrypted at
# rest when a key is configured, and is never returned by any API.
SECRET_KEYS = frozenset(
    {
        "pve_token_secret",
        "session_secret",
        "switch_password",
        "prometheus_token",
        "prometheus_password",
    }
)


KEY_FILENAME = "secret.key"


class SecretBoxError(RuntimeError):
    pass


def resolve_key(explicit_key: str, state_dir: str) -> str:
    """Find the encryption key, generating a local one if none was supplied.

    `explicit_key` is HLIDSKJALF_SECRET_KEY (which HLIDSKJALF_SECRET_KEY_FILE also
    feeds — see config.FILE_BACKED). Failing that we generate our own. The module
    docstring says what each of those is actually worth.
    """
    if explicit_key:
        return explicit_key.strip()

    # Default: our own key, in its own file, beside (not inside) the database.
    path = Path(state_dir) / KEY_FILENAME
    if path.is_file():
        return path.read_text().strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = base64.urlsafe_b64encode(_secrets.token_bytes(32)).decode()
    # O_EXCL + mode 0600 up front: never even briefly world-readable. If two
    # workers race on first boot, the loser reads the winner's key rather than
    # overwriting it — a clobbered key would make the stored secrets unreadable.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    try:
        os.write(fd, key.encode())
    finally:
        os.close(fd)
    log.warning(
        "generated a local encryption key at %s (0600). Stored secrets are not "
        "plaintext, but this key sits on the same disk as the database — set "
        "HLIDSKJALF_SECRET_KEY (systemd-creds / Docker secret) to protect against "
        "a stolen disk or backup.",
        path,
    )
    return key


def _fernet(key_material: str):
    """Build a Fernet from arbitrary key material.

    Accepts either a real 32-byte urlsafe-base64 Fernet key, or any passphrase
    (which we stretch with SHA-256). Stretching a passphrase is not a KDF against
    an offline attacker, but the realistic key sources above hand you high-entropy
    material anyway — this just means a human-chosen key still works.
    """
    from cryptography.fernet import Fernet  # imported lazily: optional feature

    raw = key_material.strip()
    try:
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            return Fernet(raw.encode())
    except Exception:
        pass
    digest = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def encrypt(value: str, key_material: str) -> str:
    """Encrypt a secret for storage. No key configured -> store as-is."""
    if not key_material or not value:
        return value
    token = _fernet(key_material).encrypt(value.encode()).decode()
    return f"{PREFIX}{token}"


def decrypt(value: str, key_material: str) -> str:
    """Decrypt a stored secret. Plaintext (pre-encryption) values pass through."""
    if not is_encrypted(value):
        return value
    if not key_material:
        raise SecretBoxError(
            "Stored secrets are encrypted but HLIDSKJALF_SECRET_KEY is not set. "
            "Provide the key the panel was configured with, or delete the state "
            "database to start over."
        )
    from cryptography.fernet import InvalidToken

    try:
        return _fernet(key_material).decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken as e:
        raise SecretBoxError(
            "HLIDSKJALF_SECRET_KEY does not decrypt the stored secrets — wrong key?"
        ) from e


def encrypt_config(values: dict[str, str], key_material: str) -> dict[str, str]:
    """Encrypt the secret-bearing entries of a config map before it is persisted."""
    return {
        k: encrypt(v, key_material) if k in SECRET_KEYS else v for k, v in values.items()
    }


def decrypt_config(values: dict[str, str], key_material: str) -> dict[str, str]:
    """Inverse of `encrypt_config`, for config loaded out of the database."""
    return {
        k: decrypt(v, key_material) if k in SECRET_KEYS else v for k, v in values.items()
    }
