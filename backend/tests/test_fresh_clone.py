"""A `git clone` of this repo must know nothing about anyone's homelab.

Whoever clones Hlidskjalf should get a *fresh install*: start it, meet the setup
wizard, configure their own Proxmox. Not ours. This test enforces both halves:

1. No tracked file carries site identity — a real TLS cert fingerprint or an
   API-token-shaped secret. (The author's Proxmox cert pin sat in `handoff.md`
   for a week before anyone noticed. It is not a credential, but it is nobody
   else's business, and the only thing that keeps it out is a test.)
2. The panel's own defaults, with no environment at all, describe *no* particular
   deployment: no host, no VLANs, no protected VMIDs, no person's username.

Site-specific facts belong in `dev/site-notes.md` and `dev/dev.env` — both
gitignored (see test_gitignore.py).
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 32 colon-separated hex pairs: a SHA-256 certificate fingerprint. Doc placeholders
# ("AA:BB:...:FF", "<your-pve-cert-sha256-fingerprint>") are far shorter and do not
# match — which is the point: a real pin is unmistakable.
FINGERPRINT_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}\b")

# A PVE API token secret is a UUID. Placeholders ("xxxxxxxx-xxxx-...") are not hex.
TOKEN_SECRET_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# Binary/vendored files git tracks that are not worth scanning as text.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf"}


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [
        ROOT / p
        for p in out.split("\0")
        if p and Path(p).suffix.lower() not in SKIP_SUFFIXES
    ]


@pytest.mark.parametrize(
    "pattern,what",
    [
        (FINGERPRINT_RE, "a real SHA-256 TLS certificate fingerprint"),
        (TOKEN_SECRET_RE, "a UUID — a PVE API token secret is shaped exactly like this"),
    ],
    ids=["fingerprint", "token-secret"],
)
def test_no_site_identity_in_tracked_files(pattern, what):
    hits = []
    for path in _tracked_text_files():
        if path.resolve() == Path(__file__).resolve():
            continue  # this file names the patterns it forbids
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}")
    assert not hits, (
        f"{what} appears in tracked files: {hits}. This repo ships to other people — "
        "put your own host's facts in dev/site-notes.md (gitignored), not in git."
    )


def test_defaults_are_nobody_s_deployment(monkeypatch):
    """With no HLIDSKJALF_* env at all, the panel must be unconfigured — that is
    what makes a clone show the setup wizard instead of someone else's homelab."""
    import os

    from hlidskjalf.config import Settings

    for key in [k for k in os.environ if k.startswith("HLIDSKJALF_")]:
        monkeypatch.delenv(key, raising=False)
    s = Settings()

    assert s.pve_host == ""              # no host: the wizard asks for it
    assert s.pve_token_secret == ""      # no credential ships in the repo
    assert s.pve_fingerprint == ""
    assert s.vlan_gateways == {}         # no one's VLANs
    assert s.protected_vmids == []       # no one's VMIDs
    assert s.bandwidth_quotas == {}
    assert s.default_ssh_keys == ""
    assert s.switch_host == ""           # the Switch page hides itself
    assert s.rescue_iso == ""
    assert s.admin_user == "admin"       # a role, not a person
    assert s.pve_node == "pve"           # Proxmox's own default node name
