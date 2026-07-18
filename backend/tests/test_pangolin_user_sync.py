"""Tenant identity sync: panel user lifecycle -> Pangolin org membership.

When `pangolin_user_sync_active` and the new panel user carries an email, the
panel invites that email into the Pangolin org's tenant role (so the tenant can
pass the panel resource's Platform SSO wall), and removes the edge identity
when the panel user is deleted. Proven here against dev/mock_pangolin.py:

1. create-with-email invites (sendEmail stays False — the LINK is relayed by
   the admin, never emailed by Pangolin, never stored by us);
2. no email or sync disabled -> no Pangolin call at all;
3. an invite failure still creates the panel user (state 'error') and the
   retry endpoint recovers it;
4. the refresh flips 'invited' -> 'active' once the friend accepts;
5. delete offboards — org user removed, unaccepted invite cancelled, and the
   email-match guard refuses to nuke a pre-existing account.

Fixtures (mock server + enabled settings) are imported from test_pangolin —
same real-uvicorn harness.
"""

import httpx
import pytest
from conftest import csrf_headers
from test_pangolin import mock_pangolin, pangolin_enabled  # noqa: F401  (fixtures)

from hlidskjalf.config import get_settings
from hlidskjalf import pangolin


@pytest.fixture
def pangolin_sync(pangolin_enabled, monkeypatch):
    """pangolin_enabled + the operator opt-in for user sync."""
    s = get_settings()
    monkeypatch.setattr(s, "pangolin_sync_users", True)
    monkeypatch.setattr(s, "pangolin_tenant_role", "Member")
    assert s.pangolin_user_sync_active
    return pangolin_enabled


def _mkuser(auth_client, username: str, email: str | None = None):
    body = {"username": username, "password": "tenant-pass-123", "role": "user"}
    if email is not None:
        body["email"] = email
    return auth_client.post("/api/users", json=body, headers=csrf_headers(auth_client))


def _mock_state(base: str) -> dict:
    return httpx.get(f"{base}/_state", timeout=3.0).json()


def _sync_state(auth_client, username: str) -> str:
    for u in auth_client.get("/api/users").json():
        if u["username"] == username:
            return u.get("pangolin_state") or ""
    return "?"


def test_invite_on_create_with_email(auth_client, pangolin_sync):
    r = _mkuser(auth_client, "sync-alice", "alice@example.com")
    assert r.status_code == 201, r.text
    pg = r.json().get("pangolin")
    assert pg and pg["state"] == "invited"
    assert pg["inviteLink"].startswith("https://pangolin.example.invalid/invite")
    assert "expiresAt" in pg

    invites = [i for i in _mock_state(pangolin_sync)["invites"] if i["email"] == "alice@example.com"]
    assert len(invites) == 1
    assert invites[0]["sendEmail"] is False  # admin relays the link, not Pangolin's SMTP
    assert invites[0]["roleId"] == 2  # Member
    assert _sync_state(auth_client, "sync-alice") == "invited"


def test_no_invite_without_email(auth_client, pangolin_sync):
    before = len(_mock_state(pangolin_sync)["invites"])
    r = _mkuser(auth_client, "sync-noemail")
    assert r.status_code == 201, r.text
    assert "pangolin" not in r.json()
    assert len(_mock_state(pangolin_sync)["invites"]) == before


def test_no_invite_when_sync_disabled(auth_client, pangolin_enabled):
    """pangolin_enabled WITHOUT the sync opt-in: nothing happens."""
    before = len(_mock_state(pangolin_enabled)["invites"])
    r = _mkuser(auth_client, "sync-off", "off@example.com")
    assert r.status_code == 201, r.text
    assert "pangolin" not in r.json()
    assert len(_mock_state(pangolin_enabled)["invites"]) == before
    assert _sync_state(auth_client, "sync-off") == ""


def test_bad_email_rejected(auth_client, pangolin_sync):
    r = _mkuser(auth_client, "sync-badmail", "not-an-email")
    assert r.status_code == 400


def test_invite_failure_marks_error_then_retry(auth_client, pangolin_sync):
    httpx.post(f"{pangolin_sync}/_fail_next/invite", timeout=3.0)
    r = _mkuser(auth_client, "sync-carol", "carol@example.com")
    assert r.status_code == 201, r.text  # the panel user stands regardless
    pg = r.json().get("pangolin")
    assert pg and pg["state"] == "error"
    assert "inviteLink" not in pg
    assert _sync_state(auth_client, "sync-carol") == "error"

    # retry via the sync endpoint -> invited now
    r2 = auth_client.post("/api/users/sync-carol/pangolin-sync", headers=csrf_headers(auth_client))
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"] == "invited"
    assert r2.json()["inviteLink"]
    assert _sync_state(auth_client, "sync-carol") == "invited"


def test_refresh_flips_active_after_accept(auth_client, pangolin_sync):
    r = _mkuser(auth_client, "sync-dave", "dave@example.com")
    assert r.status_code == 201 and r.json()["pangolin"]["state"] == "invited"

    # the friend "clicks the link"
    httpx.post(f"{pangolin_sync}/_accept/dave@example.com", timeout=3.0)

    r2 = auth_client.post("/api/users/sync-dave/pangolin-sync", headers=csrf_headers(auth_client))
    assert r2.status_code == 200 and r2.json()["state"] == "active"
    assert _sync_state(auth_client, "sync-dave") == "active"


def test_delete_offboards_org_user(auth_client, pangolin_sync):
    _mkuser(auth_client, "sync-erin", "erin@example.com")
    httpx.post(f"{pangolin_sync}/_accept/erin@example.com", timeout=3.0)
    assert any(u["username"] == "sync-erin" or u["email"] == "erin@example.com"
               for u in _mock_state(pangolin_sync)["org_users"])

    r = auth_client.delete("/api/users/sync-erin", headers=csrf_headers(auth_client))
    assert r.status_code == 200, r.text
    assert not any(u["email"] == "erin@example.com"
                   for u in _mock_state(pangolin_sync)["org_users"])


def test_delete_cancels_unaccepted_invite(auth_client, pangolin_sync):
    _mkuser(auth_client, "sync-frank", "frank@example.com")
    assert any(i["email"] == "frank@example.com" for i in _mock_state(pangolin_sync)["invites"])

    r = auth_client.delete("/api/users/sync-frank", headers=csrf_headers(auth_client))
    assert r.status_code == 200, r.text
    assert not any(i["email"] == "frank@example.com" for i in _mock_state(pangolin_sync)["invites"])


def test_delete_skips_email_mismatch(auth_client, pangolin_sync):
    """A pre-existing Pangolin account with the same username but a DIFFERENT
    email is not ours to delete — the guard must leave it alone."""
    _mkuser(auth_client, "mallory", "mallory@example.com")
    # someone else's org account, same username, different email:
    httpx.post(f"{pangolin_sync}/org/example-org/create-invite",
               json={"email": "mallory@evil.example", "roleId": 2, "validHours": 72},
               timeout=3.0)
    httpx.post(f"{pangolin_sync}/_accept/mallory@evil.example", timeout=3.0)
    assert any(u["email"] == "mallory@evil.example" for u in _mock_state(pangolin_sync)["org_users"])

    r = auth_client.delete("/api/users/mallory", headers=csrf_headers(auth_client))
    assert r.status_code == 200, r.text
    assert any(u["email"] == "mallory@evil.example" for u in _mock_state(pangolin_sync)["org_users"])


@pytest.mark.asyncio
async def test_role_id_by_name(pangolin_sync):
    s = get_settings()
    async with pangolin.PangolinClient(s) as client:
        assert await client.role_id_by_name("Member") == 2
        assert await client.role_id_by_name("member") == 2  # case-insensitive
        with pytest.raises(pangolin.PangolinError, match="tenant role"):
            await client.role_id_by_name("NoSuchRole")
