"""Apply an update from GitHub — the panel updating itself, in place.

**This endpoint executes code fetched from the internet. That is its purpose, and
that is why it is fenced.** It is off unless `HLIDSKJALF_ALLOW_SELF_UPDATE=true`,
and even then every one of these must hold:

- an **admin** session, a valid **CSRF** token, a **typed confirmation**, and a rate
  limit of 3/hour;
- the install is a **git checkout**. Docker and Nix are REFUSED, not worked around:
  a container cannot replace its own image, and a panel that tries to `docker exec`
  its way out of its own container is a privilege-escalation surface. We say what to
  run instead;
- the working tree is **clean** — an update must never eat someone's local changes;
- `origin` actually points at the **configured repo**. Otherwise "update" would mean
  "run whatever some other remote is serving";
- the target commit is **exactly the one the UI showed the operator**, and it must be
  a **fast-forward** from HEAD. Not "whatever origin/main says right now" — that
  closes the window in which the thing you approved and the thing you get differ.

Then, in order: back up the database, fast-forward, reinstall deps, rebuild the SPA,
**prove the new code even imports**, and on any failure **roll back to the old commit**
before returning. Only a run that survives all of that restarts the process.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import journal
from ..auth import rate_limited, require_csrf
from ..db import Db
from ..deps import get_db, settings
from .version import _repo_root, deployment_kind, fetch_remote, update_command

log = logging.getLogger("hlidskjalf.update")
router = APIRouter()

CONFIRM_PHRASE = "update"
STEP_TIMEOUT = 600  # npm ci on a slow box is genuinely this slow


class ApplyUpdate(BaseModel):
    confirm: str
    # The commit the operator actually saw and approved in the UI.
    target: str


def _run(cmd: list[str], cwd: Path, timeout: int = STEP_TIMEOUT) -> tuple[bool, str]:
    """-> (ok, combined output). Never raises."""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out[-4000:]


def _venv_python(root: Path) -> str:
    """The interpreter this panel runs under — that is the venv to install into."""
    return sys.executable


def _backup_db() -> str | None:
    """Copy the sqlite file aside before new code can migrate it. Never raises."""
    s = settings()
    db = Path(s.db_path)
    if not db.exists():
        return None
    dest = db.with_name(f"{db.name}.bak-preupdate-{int(time.time())}")
    try:
        shutil.copy2(db, dest)
        return str(dest)
    except OSError as e:
        log.warning("could not back up %s: %s", db, e)
        return None


@router.post("/api/update")
async def apply_update(
    body: ApplyUpdate,
    request: Request,
    db: Db = Depends(get_db),
    username: str = Depends(rate_limited("panel.update", 3, 3600.0)),
    _csrf=Depends(require_csrf),
):
    s = settings()
    user = await db.get_user_by_username(username)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")

    async def refuse(status: int, detail: str):
        await journal.record(db, request, username, journal.PANEL_UPDATE, "self",
                             f"refused: {detail}", ok=False)
        raise HTTPException(status, detail)

    if not s.allow_self_update:
        await refuse(
            403,
            "Self-update is disabled. It runs code fetched from the internet, so it is "
            "off unless HLIDSKJALF_ALLOW_SELF_UPDATE=true is set on the host.",
        )

    if body.confirm != CONFIRM_PHRASE:
        await refuse(400, f"Type '{CONFIRM_PHRASE}' to confirm")

    kind = deployment_kind()
    if kind != "git":
        await refuse(
            400,
            f"This is a {kind} install — the panel cannot replace its own "
            f"{'image' if kind == 'docker' else 'system'}, and pretending otherwise "
            f"would be a footgun. Run this on the host instead: {update_command(kind, s.update_repo)}",
        )

    root = _repo_root()
    if root is None:
        await refuse(400, "No git checkout found — cannot update in place")

    ok, dirty = _run(["git", "status", "--porcelain"], root, timeout=30)
    if not ok:
        await refuse(500, f"git status failed: {dirty}")
    if dirty:
        await refuse(
            409,
            "The working tree has uncommitted changes. Refusing to update — an update "
            "must never overwrite local work. Commit or stash them first.",
        )

    # `origin` must be the repo we told the operator we were tracking. Otherwise
    # "apply update" means "execute whatever some other remote is serving".
    ok, remote = _run(["git", "remote", "get-url", "origin"], root, timeout=30)
    if not ok or s.update_repo.lower() not in remote.lower():
        await refuse(
            400,
            f"origin ({remote or 'unset'}) does not point at the configured update repo "
            f"({s.update_repo}). Refusing to pull code from an unexpected remote.",
        )

    old_sha = ""
    ok, old_sha = _run(["git", "rev-parse", "HEAD"], root, timeout=30)
    if not ok:
        await refuse(500, f"git rev-parse failed: {old_sha}")

    # The target must be the commit the operator SAW. Re-resolve it from GitHub and
    # refuse if it moved, rather than silently shipping something else.
    try:
        remote_tip = await fetch_remote(s.update_repo, s.update_branch)
    except Exception as e:
        await refuse(502, f"could not reach GitHub: {type(e).__name__}")
    if remote_tip["commit"] != body.target:
        await refuse(
            409,
            "The branch moved since the panel showed you this update "
            f"({body.target[:8]} → {remote_tip['commit'][:8]}). Check again and re-confirm.",
        )
    target = remote_tip["commit"]

    ok, out = _run(["git", "fetch", "origin", s.update_branch], root)
    if not ok:
        await refuse(502, f"git fetch failed: {out}")

    # Fast-forward only: HEAD must be an ancestor of the target. A merge or a rebase
    # here would be the panel resolving conflicts in its own source, unattended.
    ok, _ = _run(["git", "merge-base", "--is-ancestor", old_sha, target], root, timeout=30)
    if not ok:
        await refuse(
            409,
            "This checkout is not a fast-forward from the target (it has diverged). "
            "Refusing to merge or rebase the panel's own source unattended.",
        )

    backup = _backup_db()
    log.warning("SELF-UPDATE %s -> %s (by %s), db backup: %s",
                old_sha[:8], target[:8], username, backup or "none")

    def rollback(reason: str) -> None:
        rb_ok, rb_out = _run(["git", "reset", "--hard", old_sha], root)
        log.error("self-update failed (%s); rollback to %s: %s",
                  reason, old_sha[:8], "ok" if rb_ok else rb_out)

    steps: list[tuple[str, list[str], Path]] = [
        ("fast-forward", ["git", "merge", "--ff-only", target], root),
        ("backend deps", [_venv_python(root), "-m", "pip", "install", "-q", "-e", "./backend"], root),
    ]
    # The SPA is served as static files, so stale assets would talk to new APIs.
    if (root / "frontend" / "package.json").exists() and shutil.which("npm"):
        steps += [
            ("frontend deps", ["npm", "ci", "--no-audit", "--no-fund"], root / "frontend"),
            ("frontend build", ["npm", "run", "build"], root / "frontend"),
        ]

    log_lines: list[str] = []
    for label, cmd, cwd in steps:
        ok, out = await asyncio.to_thread(_run, cmd, cwd)
        log_lines.append(f"[{label}] {'ok' if ok else 'FAILED'}")
        if not ok:
            rollback(label)
            await journal.record(db, request, username, journal.PANEL_UPDATE, "self",
                                 f"failed at {label}, rolled back to {old_sha[:8]}", ok=False)
            raise HTTPException(500, f"Update failed at '{label}' and was rolled back "
                                     f"to {old_sha[:8]}.\n\n{out}")

    # Does the new code even import? If not, restarting would leave a dead panel and
    # nobody to roll it back — so prove it in a subprocess FIRST.
    ok, out = await asyncio.to_thread(
        _run, [_venv_python(root), "-c", "import hlidskjalf.main"], root / "backend", 120
    )
    if not ok:
        rollback("import check")
        await asyncio.to_thread(
            _run, [_venv_python(root), "-m", "pip", "install", "-q", "-e", "./backend"], root
        )
        await journal.record(db, request, username, journal.PANEL_UPDATE, "self",
                             f"new code failed to import, rolled back to {old_sha[:8]}", ok=False)
        raise HTTPException(500, f"The updated code does not import — rolled back to "
                                 f"{old_sha[:8]}, nothing was restarted.\n\n{out}")

    await journal.record(db, request, username, journal.PANEL_UPDATE, "self",
                         f"{old_sha[:8]} -> {target[:8]}")

    if not s.self_update_restart:
        return {"ok": True, "from": old_sha[:8], "to": target[:8], "restarted": False,
                "db_backup": backup, "log": log_lines,
                "detail": "Update applied. Restart the panel to load it."}

    # Restart in place, AFTER the response is on the wire. execv replaces this process
    # with a fresh one running the new code — no supervisor required, same pid.
    async def restart() -> None:
        await asyncio.sleep(1.0)
        log.warning("self-update complete; re-exec'ing into %s", target[:8])
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])

    asyncio.create_task(restart())
    return {"ok": True, "from": old_sha[:8], "to": target[:8], "restarted": True,
            "db_backup": backup, "log": log_lines,
            "detail": "Update applied. The panel is restarting — this page will reconnect."}
