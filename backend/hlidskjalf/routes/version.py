"""Version + update detection.

The panel knows which commit it is running and asks GitHub what the tip of the
release branch is. If they differ, an update is available and the panel says so
— and tells you the exact command for *your* deployment, because the honest
answer differs: a container cannot replace its own image, a Nix system updates
from its flake, and only a git/venv install can pull in place.

Three rules this module does not break:

1. **Fail soft.** No network, rate-limited, no GitHub — the panel simply does
   not offer an update. It never blocks, never 500s, never nags.
2. **Never phone home with anything identifying.** The only outbound call is an
   unauthenticated GET of a public repo's commit list. No install id, no host
   name, no fleet data. It is the same request `curl` would make.
3. **Detection only.** Nothing here mutates the installation. Applying an update
   is a separate, opt-in, admin-gated action (see handoff.md) — an endpoint that
   can run arbitrary new code is a bigger hole than anything an audit has found,
   so it does not exist by default.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends

from ..auth import require_admin_user
from ..deps import settings

log = logging.getLogger("hlidskjalf.version")
router = APIRouter()

CACHE_TTL = 900.0  # 15 min: GitHub allows 60 unauthenticated calls/hour/IP
GITHUB_TIMEOUT = 6.0

_cache: dict[str, tuple[float, dict]] = {}


def _package_version() -> str:
    try:
        return pkg_version("hlidskjalf")
    except PackageNotFoundError:  # running from a source tree without an install
        return "unknown"


def _repo_root() -> Path | None:
    """The git checkout this code is running from, if it is one."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("git %s failed: %s", args, e)
        return ""


def deployment_kind() -> str:
    """How this panel was installed — it decides what "update" even means."""
    if Path("/.dockerenv").exists() or os.environ.get("HLIDSKJALF_IN_DOCKER"):
        return "docker"
    if sys.prefix.startswith("/nix/store") or os.environ.get("NIX_STORE"):
        return "nix"
    if _repo_root():
        return "git"
    return "package"


def local_state() -> dict:
    """What we are running right now. Never raises."""
    root = _repo_root()
    state = {
        "version": _package_version(),
        "commit": "",
        "branch": "",
        "dirty": False,
        "deployment": deployment_kind(),
    }
    if root:
        state["commit"] = _git(root, "rev-parse", "HEAD")
        state["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        # Uncommitted work is INDEPENDENT of being behind: you can have local edits
        # AND be behind upstream at the same time (the common case on a dev box).
        # It does not mean "ahead" — it means an update would overwrite something,
        # so routes/update.py refuses until the tree is clean.
        state["dirty"] = bool(_git(root, "status", "--porcelain"))
    return state


async def fetch_remote(repo: str, branch: str) -> dict:
    """The tip of `branch` on GitHub. Raises httpx errors; the caller degrades."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
        r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json()
    commit = data.get("commit") or {}
    return {
        "commit": data.get("sha", ""),
        "message": (commit.get("message") or "").split("\n")[0][:200],
        "date": (commit.get("author") or {}).get("date", ""),
        "author": (commit.get("author") or {}).get("name", ""),
    }


async def fetch_behind(repo: str, base: str, head: str) -> dict | None:
    """How far `base` (us) is behind `head` (GitHub), and with which commits.

    Returns None when GitHub cannot compare them — which happens legitimately:
    a local commit that was never pushed is simply unknown to GitHub.
    """
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    try:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log.debug("compare %s...%s failed: %s", base[:8], head[:8], e)
        return None
    return {
        "behind_by": data.get("ahead_by", 0),  # from base's perspective: commits to pull
        "commits": [
            {
                "sha": c.get("sha", "")[:8],
                "message": ((c.get("commit") or {}).get("message") or "").split("\n")[0][:120],
            }
            for c in (data.get("commits") or [])[-20:]
        ],
    }


def normalize_version(v: str) -> str:
    """Compare "v0.4.2-alpha" (a git tag) with "0.4.2a0" (what Python reports).

    setuptools normalises PEP 440 versions, so the version the running package
    reports never looks like the tag someone pushed. Canonicalise both ends rather
    than telling an operator they are out of date because of punctuation.
    """
    v = v.strip().lower().lstrip("v")
    for word, short in (("-alpha", "a"), ("-beta", "b"), ("-rc", "rc"),
                        ("alpha", "a"), ("beta", "b")):
        v = v.replace(word, short)
    v = v.replace("-", "").replace("_", "").replace(".post", "post")
    # 0.4.2a == 0.4.2a0
    if v and v[-1] in "ab" :
        v += "0"
    return v


async def fetch_latest_release(repo: str) -> dict | None:
    """The newest tag on GitHub. The only thing a non-git install can compare against.

    A Nix or Docker deployment has no commit to compare — `/nix/store/...` is not a
    checkout — so commit-based detection reports "this install does not expose a git
    commit" and the Updates page is decoration. Releases are the unit those installs
    actually move between, so compare those.
    """
    url = f"https://api.github.com/repos/{repo}/tags"
    try:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            tags = r.json()
    except httpx.HTTPError as e:
        log.debug("tag lookup for %s failed: %s", repo, e)
        return None
    if not tags:
        return None
    tag = tags[0]  # GitHub returns newest first
    return {"tag": tag.get("name", ""), "commit": (tag.get("commit") or {}).get("sha", "")}


def update_command(kind: str, repo: str) -> str:
    """The honest way to apply an update for THIS deployment.

    A panel that tries to `docker exec` its way out of its own container is a
    footgun and a privilege-escalation surface. So we say what to run; we do not
    pretend we can do it ourselves.
    """
    return {
        "docker": "docker compose pull && docker compose up -d",
        "nix": "nix flake update hlidskjalf && sudo nixos-rebuild switch",
        "git": (
            "git pull --ff-only && "
            ".venv/bin/pip install -e ./backend && "
            "(cd frontend && npm ci && npm run build) && "
            "sudo systemctl restart hlidskjalf"
        ),
        "package": f"pip install --upgrade 'hlidskjalf @ git+https://github.com/{repo}'",
    }.get(kind, "")


async def _compute(force: bool) -> dict:
    s = settings()
    repo, branch = s.update_repo, s.update_branch
    # local_state() shells out to git three times (up to 5s each) — keep that
    # off the event loop, same as routes/update.py does for its subprocesses.
    local = await asyncio.to_thread(local_state)
    view = {
        **local,
        "repo": repo,
        "branch_tracked": branch,
        "latest": None,
        # The newest published tag. What a non-git install (nix/docker/pip) can
        # actually compare itself against — those move between releases, not commits.
        "latest_release": None,
        "update_available": False,
        "behind_by": 0,
        "commits": [],
        "command": update_command(local["deployment"], repo),
        "notes_url": f"https://github.com/{repo}/commits/{branch}",
        # Whether the panel may apply the update itself (routes/update.py). Only a
        # git checkout can, and only when the operator explicitly allowed it on the
        # host — the UI must not offer a button that is guaranteed to 403.
        "self_update": s.allow_self_update and local["deployment"] == "git",
        "error": None,
        "checked_at": time.time(),
    }

    if not s.update_check_enabled:
        view["error"] = "update checks are disabled (HLIDSKJALF_UPDATE_CHECK_ENABLED=false)"
        return view

    cached = _cache.get(f"{repo}@{branch}")
    if cached and not force and time.monotonic() - cached[0] < CACHE_TTL:
        remote = cached[1]
    else:
        try:
            remote = await fetch_remote(repo, branch)
        except (httpx.HTTPError, ValueError) as e:
            # Offline, rate-limited, DNS gone: not an error worth shouting about.
            view["error"] = f"could not reach GitHub: {type(e).__name__}"
            return view
        _cache[f"{repo}@{branch}"] = (time.monotonic(), remote)

    view["latest"] = remote

    if not local["commit"]:
        # Not a git checkout (docker/nix/pip). Commits are meaningless here — but
        # RELEASES are exactly what these installs move between, so compare those.
        release = await fetch_latest_release(repo)
        if release is None or not release["tag"]:
            view["error"] = "no releases published yet — compare versions manually"
            return view
        view["latest_release"] = release["tag"]
        view["update_available"] = (
            normalize_version(release["tag"]) != normalize_version(local["version"])
        )
        return view

    if local["commit"] == remote["commit"]:
        return view  # up to date

    compare = await fetch_behind(repo, local["commit"], remote["commit"])
    if compare is None:
        view["error"] = "GitHub does not know this commit — is it pushed?"
        return view

    view["behind_by"] = compare["behind_by"]
    view["commits"] = compare["commits"]
    # Behind == there is something to pull. Ahead-only (behind_by == 0) means
    # this checkout has commits GitHub has not seen — not an update, the reverse.
    view["update_available"] = compare["behind_by"] > 0
    return view


@router.get("/api/version")
async def get_version(force: bool = False, _admin: dict = Depends(require_admin_user)):
    """Current vs latest. Admin-only: it names the deployment and its commit."""
    try:
        return await _compute(force)
    except Exception as e:  # a broken update check must never break the panel
        log.warning("update check failed: %s", e)
        local = await asyncio.to_thread(local_state)
        return {
            **local,
            "latest": None,
            "update_available": False,
            "behind_by": 0,
            "commits": [],
            "command": "",
            "self_update": False,
            "error": f"update check failed: {type(e).__name__}",
            "checked_at": time.time(),
        }
