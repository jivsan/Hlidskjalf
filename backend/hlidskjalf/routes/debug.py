"""Debug section endpoints (admin only).

Enabled when HLIDSKJALF_DEBUG=true (or settings.debug).

Provides:
- Config (redacted)
- Detailed health
- Recent errors (populated by global handler)
- Recent logs (via in-memory handler)
- Accumulator status

All responses are admin-gated.
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from .. import secretbox
from ..auth import require_admin_user
from ..config import FILE_BACKED, get_settings
from ..db import Db
from ..deps import get_db

router = APIRouter(tags=["debug"])

# In-memory buffers (limit 100)
recent_logs: list[dict[str, Any]] = []
recent_errors: list[dict[str, Any]] = []

def _append_recent(buf: list[dict], entry: dict, limit: int = 100) -> None:
    buf.append(entry)
    if len(buf) > limit:
        del buf[: len(buf) - limit]


class InMemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                "ts": time.time(),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            _append_recent(recent_logs, entry)
        except Exception:
            self.handleError(record)


# Redaction is driven by the declared secret sets, NOT by guessing from the name.
# A keyword denylist ("does it contain 'secret'?") silently leaks the first secret
# someone adds whose name doesn't happen to match — the keyword list is a guess
# about the future, and it will eventually be wrong. The keyword pass is kept only
# as a belt-and-braces second net.
_KEYWORD_NET = ("token", "secret", "password", "hash", "key")


def _is_sensitive(name: str) -> bool:
    if name in secretbox.SECRET_KEYS or name in FILE_BACKED:
        return True
    return any(word in name.lower() for word in _KEYWORD_NET)


@router.get("/config")
async def debug_config(_: dict = Depends(require_admin_user)):
    s = get_settings()
    data = s.model_dump() if hasattr(s, "model_dump") else s.__dict__.copy()
    for k in list(data.keys()):
        if _is_sensitive(k):
            data[k] = "***REDACTED***"
    data["pve_base_url"] = getattr(s, "pve_base_url", None)
    data["db_path"] = str(getattr(s, "db_path", ""))
    # Surfaced because the default is now EMPTY: nothing is protected, which means
    # an admin can destroy the VM that is running this panel.
    data["protected_vmids_empty"] = not s.protected_vmids
    return data


@router.get("/health")
async def debug_health(request: Request, _: dict = Depends(require_admin_user)):
    s = get_settings()
    state = request.app.state
    acc = getattr(state, "accumulator", None)
    acc_status = acc.get_status() if acc and hasattr(acc, "get_status") else None
    return {
        "ok": True,
        "debug": s.debug,
        "log_level": s.log_level,
        "pve_node": getattr(s, "pve_node", None),
        "db_path": str(getattr(s, "db_path", "")),
        "metrics_source": s.metrics_source,
        "state_keys": list(state.__dict__.keys()) if hasattr(state, "__dict__") else [],
        "accumulator": acc_status,
    }


@router.get("/errors")
async def debug_errors(_: dict = Depends(require_admin_user)):
    return list(reversed(recent_errors[-50:]))


@router.get("/logs")
async def debug_logs(_: dict = Depends(require_admin_user)):
    return list(reversed(recent_logs[-50:]))


@router.get("/accumulator")
async def debug_accumulator(request: Request, _: dict = Depends(require_admin_user)):
    acc = getattr(request.app.state, "accumulator", None)
    if acc and hasattr(acc, "get_status"):
        return acc.get_status()
    return {"running": False, "prev_count": 0, "note": "accumulator not available"}


# Helper for main.py global handler
def append_error(entry: dict) -> None:
    _append_recent(recent_errors, entry)


@router.get("/audit")
async def debug_audit(
    limit: int = 200,
    actor: str | None = None,
    action: str | None = None,
    _: dict = Depends(require_admin_user),
    db: Db = Depends(get_db),
):
    """The durable audit trail: who did what, to which guest, from where.

    Unlike /logs and /errors (in-memory ring buffers that die on restart), this is
    persisted — it is the record you go to after someone destroys a VM. Refused
    actions are in here too.
    """
    return await db.audit_recent(limit=limit, actor=actor, action=action)
