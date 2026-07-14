"""Per-user access control on the console + rescue endpoints.

Regressions guarded here (both were live IDOR/authz holes):

- ``GET /api/vms/{vmid}/console`` used to depend only on a valid session, so any
  logged-in tenant could mint a VNC ticket for another tenant's VM.
- ``POST``/``DELETE /api/vms/{vmid}/rescue`` used to depend only on CSRF — no
  ownership check, no admin check, and no protected-VMID guard, so anyone could
  reboot any VM (including protected infrastructure) into the rescue ISO.

The shared session ``client`` (see conftest) is the only TestClient, so these
tests log it in as the relevant principal (admin to provision the tenant, then
the tenant, or admin again) rather than juggling two cookie jars at once.
"""

from conftest import ADMIN_PASSWORD, ADMIN_USER, csrf_headers

# vmid map in dev/mock_pve.py:
#   105 vps-alpha  qemu running  (not protected)  -> the tenant's VM (A)
#   115 vps-beta  qemu running  (not protected)  -> a VM the tenant does NOT own (B)
#   120 app-01           qemu running  (not protected)  -> admin rescue happy-path
#   151 pbs              qemu running  (PROTECTED)       -> rescue refused for everyone
TENANT_USER = "tenant-ac"
TENANT_PASS = "tenant-ac-password"
TENANT_VMID = 105          # A: assigned to the regular user
OTHER_VMID = 115           # B: a different VM the regular user must not touch
ADMIN_RESCUE_VMID = 120    # non-protected, running qemu — admin happy path
PROTECTED_VMID = 151       # in HLIDSKJALF_PROTECTED_VMIDS (conftest sets 101,151)


def _login(c, username: str, password: str):
    """Log the shared session client in as `username`; stash CSRF on `.csrf`."""
    c.cookies.clear()
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.csrf = r.json()["csrf"]
    return c


def _ensure_tenant(auth_client):
    """Create the regular user assigned to TENANT_VMID (idempotent across the
    session-scoped db)."""
    r = auth_client.post(
        "/api/users",
        json={
            "username": TENANT_USER,
            "password": TENANT_PASS,
            "role": "user",
            "vmid": TENANT_VMID,
        },
        headers=csrf_headers(auth_client),
    )
    # 201 first time; 409 ("Username already exists") on later tests in the session.
    assert r.status_code in (201, 409), r.text


# --- console (IDOR) ---------------------------------------------------------


def test_user_console_denied_for_other_vm(auth_client):
    _ensure_tenant(auth_client)
    c = _login(auth_client, TENANT_USER, TENANT_PASS)
    r = c.get(f"/api/vms/{OTHER_VMID}/console")
    assert r.status_code == 403, r.text
    assert "access" in r.json()["detail"].lower()


def test_user_console_allowed_for_own_vm(auth_client):
    _ensure_tenant(auth_client)
    c = _login(auth_client, TENANT_USER, TENANT_PASS)
    r = c.get(f"/api/vms/{TENANT_VMID}/console")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ws_path"].startswith(f"/ws/console/{TENANT_VMID}?key=")
    assert body["kind"] == "qemu"


# --- rescue: ownership ------------------------------------------------------


def test_user_rescue_enter_denied_for_other_vm(auth_client):
    _ensure_tenant(auth_client)
    c = _login(auth_client, TENANT_USER, TENANT_PASS)
    r = c.post(f"/api/vms/{OTHER_VMID}/rescue", headers=csrf_headers(c))
    assert r.status_code == 403, r.text
    assert "access" in r.json()["detail"].lower()


def test_user_rescue_exit_denied_for_other_vm(auth_client):
    _ensure_tenant(auth_client)
    c = _login(auth_client, TENANT_USER, TENANT_PASS)
    r = c.request("DELETE", f"/api/vms/{OTHER_VMID}/rescue", headers=csrf_headers(c))
    assert r.status_code == 403, r.text
    assert "access" in r.json()["detail"].lower()


# --- rescue: protected VMIDs refused for EVERYONE (admin included) -----------


def test_rescue_protected_vmid_refused_for_admin(auth_client):
    # auth_client is the bootstrap admin; admins bypass ownership but NOT the
    # protected-VMID guard, because rescue power-cycles the machine.
    r = auth_client.post(
        f"/api/vms/{PROTECTED_VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 403, r.text
    assert "protected" in r.json()["detail"].lower()


# --- rescue: admin happy path on a good VM ----------------------------------


def test_admin_rescue_enter_and_exit(auth_client, mock_pve_url):
    # ADMIN_RESCUE_VMID is a non-protected, running qemu VM.
    enter = auth_client.post(
        f"/api/vms/{ADMIN_RESCUE_VMID}/rescue", headers=csrf_headers(auth_client)
    )
    assert enter.status_code == 200, enter.text
    assert enter.json()["rescue"] is True

    exit_ = auth_client.request(
        "DELETE",
        f"/api/vms/{ADMIN_RESCUE_VMID}/rescue",
        headers=csrf_headers(auth_client),
    )
    assert exit_.status_code == 200, exit_.text
    assert exit_.json()["rescue"] is False
