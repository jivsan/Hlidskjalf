"""The secrets that must never reach a commit.

This exists because they nearly did. `scripts/dev.sh --mock` puts the state dir
inside the repo (`.dev-state/`), and `.gitignore` only covered `*.sqlite3` — so a
`git add -A` on a box that had run the dev script would have staged
`.dev-state/secret.key`: the Fernet key that DECRYPTS the stored Proxmox API
token. In a public repo. The database was ignored; the key that opens it was not.

A .gitignore rule with no test behind it is how that happened. So: ask git.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Paths the panel really creates at runtime. If git would track any of these, the
# next `git add -A` publishes a secret.
MUST_BE_IGNORED = [
    ".dev-state/secret.key",          # scripts/dev.sh --mock
    ".dev-state/hlidskjalf.sqlite3",
    "secret.key",                     # HLIDSKJALF_STATE_DIR=. (someone will)
    "hlidskjalf.sqlite3",
    "hlidskjalf.sqlite3.bak-v3-1234", # migrations back the DB up before touching it
    "dev/dev.env",                    # the Proxmox token in plaintext
    "dev/site-notes.md",              # the operator's own host, pin, VMIDs
]

# ...and things that must stay tracked, so an over-broad rule can't silently
# delete the dev stack from the repo.
MUST_NOT_BE_IGNORED = [
    "dev/mock_switch.key",            # throwaway TLS key for the mock switch
    "dev/dev.env.example",
    "backend/hlidskjalf/main.py",
]


def _ignored(path: str) -> bool:
    """Ask git itself — not a regex we hope matches what git does."""
    p = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT, capture_output=True,
    )
    return p.returncode == 0  # 0 = ignored, 1 = not ignored


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_secrets_are_gitignored(path):
    assert _ignored(path), (
        f"{path} is NOT ignored by git. A `git add -A` would stage it. "
        "If this is the secret key or the database, that is a credential leak."
    )


@pytest.mark.parametrize("path", MUST_NOT_BE_IGNORED)
def test_the_rules_are_not_over_broad(path):
    assert not _ignored(path), f"{path} must stay tracked, but .gitignore excludes it"
