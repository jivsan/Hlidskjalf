"""Self-update (routes/update.py).

This endpoint runs code fetched from the internet. Every test here is about a
fence holding: if one of these ever goes green-by-accident, the panel has become
a remote-code-execution hole with a nice button on it.

No test lets a real `git`, `pip` or `npm` run — `_run` is stubbed throughout.
"""

import pytest
from conftest import csrf_headers

from hlidskjalf.config import get_settings
from hlidskjalf.routes import update as U

OLD = "a" * 40
NEW = "b" * 40


@pytest.fixture
def allow(monkeypatch):
    """Turn the feature on, and pretend we are a clean git checkout."""
    s = get_settings()
    monkeypatch.setattr(U, "deployment_kind", lambda: "git")
    monkeypatch.setattr(U, "_repo_root", lambda: __import__("pathlib").Path("/tmp"))
    monkeypatch.setattr(U, "_backup_db", lambda: "/tmp/hlidskjalf.sqlite3.bak")
    object.__setattr__(s, "allow_self_update", True)
    object.__setattr__(s, "self_update_restart", False)  # never re-exec the test runner
    yield
    object.__setattr__(s, "allow_self_update", False)


def _git(cmd, cwd, timeout=None):
    """A clean checkout on OLD, origin pointing at the configured repo."""
    if cmd[:2] == ["git", "status"]:
        return True, ""                      # clean tree
    if cmd[:3] == ["git", "remote", "get-url"]:
        return True, "git@github.com:jivsan/Hlidskjalf.git"
    if cmd[:2] == ["git", "rev-parse"]:
        return True, OLD
    if cmd[:2] == ["git", "merge-base"]:
        return True, ""                      # OLD is an ancestor of NEW: fast-forward ok
    return True, "ok"                        # fetch, merge, pip, npm, import check


async def _remote_at(sha):
    async def f(repo, branch):
        return {"commit": sha, "message": "feat: x", "date": "", "author": ""}
    return f


def _post(client, confirm="update", target=NEW):
    return client.post("/api/update", json={"confirm": confirm, "target": target},
                       headers=csrf_headers(client))


# --- the gates ---------------------------------------------------------------


async def test_disabled_by_default(auth_client, monkeypatch):
    """The feature must be OFF unless the operator turned it on ON THE HOST."""
    monkeypatch.setattr(U, "_run", _git)
    r = _post(auth_client)
    assert r.status_code == 403
    assert "HLIDSKJALF_ALLOW_SELF_UPDATE" in r.json()["detail"]


async def test_requires_admin(auth_client, allow, monkeypatch):
    monkeypatch.setattr(U, "_run", _git)
    r = auth_client.post(
        "/api/users",
        json={"username": "tenant-update", "password": "password123", "vmid": 117},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    r = auth_client.post(
        "/api/login", json={"username": "tenant-update", "password": "password123"}
    )
    auth_client.csrf = r.json()["csrf"]
    assert _post(auth_client).status_code == 403


async def test_requires_csrf(auth_client, allow, monkeypatch):
    monkeypatch.setattr(U, "_run", _git)
    r = auth_client.post("/api/update", json={"confirm": "update", "target": NEW})
    assert r.status_code == 403


async def test_requires_typed_confirmation(auth_client, allow, monkeypatch):
    monkeypatch.setattr(U, "_run", _git)
    r = _post(auth_client, confirm="yes")
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


async def test_docker_install_is_refused_not_worked_around(auth_client, allow, monkeypatch):
    """A container cannot replace its own image, and a panel that tries to
    `docker exec` its way out is a privilege-escalation surface."""
    monkeypatch.setattr(U, "_run", _git)
    monkeypatch.setattr(U, "deployment_kind", lambda: "docker")
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    r = _post(auth_client)
    assert r.status_code == 400
    assert "docker compose pull" in r.json()["detail"]


async def test_dirty_working_tree_is_refused(auth_client, allow, monkeypatch):
    """An update must never overwrite someone's local work."""

    def dirty(cmd, cwd, timeout=None):
        if cmd[:2] == ["git", "status"]:
            return True, " M backend/hlidskjalf/main.py"
        return _git(cmd, cwd, timeout)

    monkeypatch.setattr(U, "_run", dirty)
    r = _post(auth_client)
    assert r.status_code == 409
    assert "uncommitted" in r.json()["detail"]


async def test_foreign_origin_is_refused(auth_client, allow, monkeypatch):
    """Otherwise 'apply update' means 'execute whatever some other remote serves'."""

    def evil(cmd, cwd, timeout=None):
        if cmd[:3] == ["git", "remote", "get-url"]:
            return True, "git@github.com:attacker/Hlidskjalf.git"
        return _git(cmd, cwd, timeout)

    monkeypatch.setattr(U, "_run", evil)
    r = _post(auth_client)
    assert r.status_code == 400
    assert "unexpected remote" in r.json()["detail"]


async def test_target_must_be_the_commit_the_operator_approved(auth_client, allow, monkeypatch):
    """The branch moved between 'check' and 'apply' — ship nothing."""
    monkeypatch.setattr(U, "_run", _git)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at("c" * 40))
    r = _post(auth_client, target=NEW)
    assert r.status_code == 409
    assert "moved" in r.json()["detail"]


async def test_non_fast_forward_is_refused(auth_client, allow, monkeypatch):
    """The panel will not merge or rebase its own source unattended."""

    def diverged(cmd, cwd, timeout=None):
        if cmd[:2] == ["git", "merge-base"]:
            return False, ""
        return _git(cmd, cwd, timeout)

    monkeypatch.setattr(U, "_run", diverged)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    r = _post(auth_client)
    assert r.status_code == 409
    assert "fast-forward" in r.json()["detail"]


# --- rollback ----------------------------------------------------------------


async def test_failed_step_rolls_back(auth_client, allow, monkeypatch):
    calls: list[list[str]] = []

    def failing(cmd, cwd, timeout=None):
        calls.append(cmd)
        if cmd[:2] == ["git", "merge"]:
            return False, "merge exploded"
        return _git(cmd, cwd, timeout)

    monkeypatch.setattr(U, "_run", failing)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    r = _post(auth_client)
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]
    assert ["git", "reset", "--hard", OLD] in calls


async def test_code_that_does_not_import_is_rolled_back_without_restarting(
    auth_client, allow, monkeypatch
):
    """The one that saves you: a broken update would otherwise restart into a dead
    panel, with nobody left to roll it back."""
    calls: list[list[str]] = []

    def bad_import(cmd, cwd, timeout=None):
        calls.append(cmd)
        if cmd[1:3] == ["-c", "import hlidskjalf.main"]:
            return False, "SyntaxError: invalid syntax"
        return _git(cmd, cwd, timeout)

    monkeypatch.setattr(U, "_run", bad_import)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    r = _post(auth_client)
    assert r.status_code == 500
    assert "does not import" in r.json()["detail"]
    assert ["git", "reset", "--hard", OLD] in calls


# --- the happy path ----------------------------------------------------------


async def test_successful_update_backs_up_the_db_and_reports_the_move(
    auth_client, allow, monkeypatch
):
    monkeypatch.setattr(U, "_run", _git)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    r = _post(auth_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["from"] == OLD[:8] and body["to"] == NEW[:8]
    assert body["db_backup"]          # the database is copied aside BEFORE new code runs
    assert body["restarted"] is False  # self_update_restart=False in this fixture


async def test_it_is_audited(auth_client, allow, monkeypatch):
    """The panel rewriting its own code is exactly what you want in the log later."""
    monkeypatch.setattr(U, "_run", _git)
    monkeypatch.setattr(U, "fetch_remote", await _remote_at(NEW))
    assert _post(auth_client).status_code == 200
    entries = auth_client.get("/api/debug/audit").json()
    assert any(e["action"] == "panel.update" for e in entries)
