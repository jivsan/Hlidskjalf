"""Update detection (routes/version.py).

The rules that matter, in order: it must never break the panel, never lie about
being up to date, and never mutate anything. GitHub is never contacted here —
every test stubs the two fetch functions, so the suite stays offline and fast.
"""

import httpx
import pytest

from hlidskjalf.routes import version as V

from conftest import csrf_headers

LOCAL_SHA = "a" * 40
REMOTE_SHA = "b" * 40


@pytest.fixture(autouse=True)
def clear_cache():
    V._cache.clear()
    yield
    V._cache.clear()


@pytest.fixture
def as_git_checkout(monkeypatch):
    """Pretend we run from a clean git checkout sitting at LOCAL_SHA."""
    monkeypatch.setattr(
        V,
        "local_state",
        lambda: {
            "version": "0.4-alpha",
            "commit": LOCAL_SHA,
            "branch": "main",
            "dirty": False,
            "deployment": "git",
        },
    )


def _remote(sha=REMOTE_SHA):
    return {"commit": sha, "message": "fix: something", "date": "2026-07-13T20:00:00Z",
            "author": "jivsan"}


# --- authorization -----------------------------------------------------------


def test_version_needs_a_session(anon):
    assert anon.get("/api/version").status_code == 401


def test_version_is_admin_only(auth_client):
    """It names the deployment, its branch and its commit — tenants don't see it."""
    r = auth_client.post(
        "/api/users",
        json={"username": "tenant-version", "password": "password123", "vmid": 116},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 201, r.text
    assert auth_client.post(
        "/api/login", json={"username": "tenant-version", "password": "password123"}
    ).status_code == 200
    assert auth_client.get("/api/version").status_code == 403


# --- the three states --------------------------------------------------------


async def test_up_to_date_when_commits_match(auth_client, as_git_checkout, monkeypatch):
    async def fake_remote(repo, branch):
        return _remote(LOCAL_SHA)

    monkeypatch.setattr(V, "fetch_remote", fake_remote)
    body = auth_client.get("/api/version").json()
    assert body["update_available"] is False
    assert body["behind_by"] == 0
    assert body["error"] is None


async def test_update_available_when_behind(auth_client, as_git_checkout, monkeypatch):
    async def fake_remote(repo, branch):
        return _remote()

    async def fake_behind(repo, base, head):
        assert base == LOCAL_SHA and head == REMOTE_SHA
        return {"behind_by": 3, "commits": [{"sha": "c0ffee01", "message": "feat: settings"}]}

    monkeypatch.setattr(V, "fetch_remote", fake_remote)
    monkeypatch.setattr(V, "fetch_behind", fake_behind)
    body = auth_client.get("/api/version").json()
    assert body["update_available"] is True
    assert body["behind_by"] == 3
    assert body["commits"][0]["message"] == "feat: settings"
    # It must tell the operator how to actually apply it, per deployment.
    assert "git pull --ff-only" in body["command"]


async def test_ahead_only_is_not_an_update(auth_client, as_git_checkout, monkeypatch):
    """A checkout with unpushed commits is AHEAD, not behind. Offering it an
    'update' would ask the operator to overwrite their own work."""

    async def fake_remote(repo, branch):
        return _remote()

    async def fake_behind(repo, base, head):
        return {"behind_by": 0, "commits": []}  # diverged, nothing to pull

    monkeypatch.setattr(V, "fetch_remote", fake_remote)
    monkeypatch.setattr(V, "fetch_behind", fake_behind)
    assert auth_client.get("/api/version").json()["update_available"] is False


# --- fail-soft ---------------------------------------------------------------


async def test_github_unreachable_degrades_quietly(auth_client, as_git_checkout, monkeypatch):
    """No network must mean 'no update offered' — never a 500, never a nag."""

    async def boom(repo, branch):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(V, "fetch_remote", boom)
    r = auth_client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["update_available"] is False
    assert "could not reach GitHub" in body["error"]
    assert body["commit"] == LOCAL_SHA  # still reports what we ARE running


async def test_unknown_local_commit_does_not_claim_up_to_date(
    auth_client, as_git_checkout, monkeypatch
):
    """An unpushed commit is unknown to GitHub; say so rather than guess."""

    async def fake_remote(repo, branch):
        return _remote()

    async def no_compare(repo, base, head):
        return None

    monkeypatch.setattr(V, "fetch_remote", fake_remote)
    monkeypatch.setattr(V, "fetch_behind", no_compare)
    body = auth_client.get("/api/version").json()
    assert body["update_available"] is False
    assert "pushed" in body["error"]


async def test_disabled_check_never_calls_github(auth_client, as_git_checkout, monkeypatch):
    from hlidskjalf.config import get_settings

    async def must_not_run(repo, branch):
        raise AssertionError("update check ran while disabled")

    monkeypatch.setattr(V, "fetch_remote", must_not_run)
    object.__setattr__(get_settings(), "update_check_enabled", False)
    try:
        body = auth_client.get("/api/version").json()
        assert body["update_available"] is False
        assert "disabled" in body["error"]
    finally:
        object.__setattr__(get_settings(), "update_check_enabled", True)


async def test_result_is_cached(auth_client, as_git_checkout, monkeypatch):
    """GitHub allows 60 anonymous calls/hour — one panel must not burn them."""
    calls = 0

    async def counting(repo, branch):
        nonlocal calls
        calls += 1
        return _remote(LOCAL_SHA)

    monkeypatch.setattr(V, "fetch_remote", counting)
    auth_client.get("/api/version")
    auth_client.get("/api/version")
    assert calls == 1
    auth_client.get("/api/version?force=true")  # the "check now" button bypasses it
    assert calls == 2


# --- deployment awareness ----------------------------------------------------


def test_update_command_is_honest_per_deployment():
    """A container cannot replace its own image; Nix updates from the flake.
    Pretending otherwise would be a footgun (and a privilege-escalation surface)."""
    assert "docker compose pull" in V.update_command("docker", "jivsan/Hlidskjalf")
    assert "nixos-rebuild" in V.update_command("nix", "jivsan/Hlidskjalf")
    assert "git pull --ff-only" in V.update_command("git", "jivsan/Hlidskjalf")
