"""Server-side safety rails: protected VMIDs and destroy name confirmation."""

from conftest import csrf_headers

PROTECTED = 151  # pbs, in HLIDSKJALF_PROTECTED_VMIDS


def test_stop_protected_vmid_refused(auth_client):
    r = auth_client.post(
        f"/api/vms/{PROTECTED}/status/stop", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "protected" in detail
    assert str(PROTECTED) in detail


def test_reset_protected_vmid_refused(auth_client):
    r = auth_client.post(
        f"/api/vms/{PROTECTED}/status/reset", headers=csrf_headers(auth_client)
    )
    assert r.status_code == 403


def test_shutdown_and_reboot_protected_vmid_allowed(auth_client):
    h = csrf_headers(auth_client)
    r = auth_client.post(f"/api/vms/{PROTECTED}/status/shutdown", headers=h)
    assert r.status_code == 200
    # bring it back so later fleet assertions still see it
    r = auth_client.post(f"/api/vms/{PROTECTED}/status/start", headers=h)
    assert r.status_code == 200
    r = auth_client.post(f"/api/vms/{PROTECTED}/status/reboot", headers=h)
    assert r.status_code == 200


def test_destroy_wrong_confirm_name_400(auth_client):
    r = auth_client.request(
        "DELETE",
        "/api/vms/140",
        json={"confirm_name": "definitely-not-its-name"},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 400
    assert "confirm_name" in r.json()["detail"]
    # and the guest is still there
    assert auth_client.get("/api/vms/140").status_code == 200


def test_destroy_protected_vmid_refused(auth_client):
    r = auth_client.request(
        "DELETE",
        f"/api/vms/{PROTECTED}",
        json={"confirm_name": "pbs"},  # even with the correct name
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 403
    assert "protected" in r.json()["detail"]


def test_reinstall_protected_vmid_refused(auth_client):
    r = auth_client.post(
        f"/api/vms/{PROTECTED}/reinstall",
        json={"template_vmid": 9000, "confirm_name": "pbs"},
        headers=csrf_headers(auth_client),
    )
    assert r.status_code == 403
