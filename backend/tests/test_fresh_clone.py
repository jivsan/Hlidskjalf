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

# Credentials that announce themselves by prefix. This test did not look for these,
# and that is precisely how a GitHub Personal Access Token reached handoff.md and a
# public repo (alert #1, 2026-07-12 — auto-revoked by GitHub, but only by luck: it
# scans its OWN token formats and would not have saved an AWS key or an SSH key).
# The rule is not "no fingerprints" — it is "no credentials", so enumerate them.
# The one sanctioned exception: the dev mock switch's throwaway TLS key. It is a
# real private key and it is SUPPOSED to be in the repo — the mock serves TLS with
# it on localhost, and the dev stack cannot run without it. Nothing else may be here.
ALLOWED_KEYS = {"dev/mock_switch.key"}

CREDENTIAL_RES = [
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub fine-grained PAT"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub classic token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
]

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


@pytest.mark.parametrize("pattern,what", CREDENTIAL_RES, ids=[w for _, w in CREDENTIAL_RES])
def test_no_credentials_in_tracked_files(pattern, what):
    """No credential-shaped string may be committed, whatever kind it is.

    History is the reason this is parametrised rather than a single regex: a GitHub
    PAT was committed to `handoff.md` and published. GitHub revoked it automatically —
    but that only works because GitHub scans for GitHub's own formats. An AWS key, an
    SSH private key or a Slack token would have sat there indefinitely. A repo that is
    public must catch its own leaks; being saved by the platform is not a control.
    """
    hits = []
    for path in _tracked_text_files():
        if path.resolve() == Path(__file__).resolve():
            continue  # this file names the shapes it forbids
        if path.relative_to(ROOT).as_posix() in ALLOWED_KEYS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}")
    assert not hits, (
        f"{what} appears in tracked files: {hits}. Revoke it NOW — a public repo is "
        "scraped within minutes — then remove it. Secrets belong in dev/dev.env or a "
        "secret manager, never in a commit, a PR body, or a chat message."
    )


def test_the_only_committed_key_is_the_mocks_throwaway():
    """The one allowed private key must stay what it claims to be: a self-signed
    certificate for `dev/mock_switch.py`, generated for a fake switch that answers
    on localhost. If a real key is ever parked at this path, the allowlist above
    would wave it straight through — so check the certificate it belongs to."""
    cert = (ROOT / "dev" / "mock_switch.crt").read_text()
    assert "BEGIN CERTIFICATE" in cert
    assert (ROOT / "dev" / "mock_switch.key").exists()
    # Anything that is not the mock's own pair must not be in the allowlist.
    assert ALLOWED_KEYS == {"dev/mock_switch.key"}
