"""Fleet list and VM detail against the mock PVE."""


def test_fleet_lists_guests_with_protected_flags(auth_client):
    r = auth_client.get("/api/vms")
    assert r.status_code == 200
    vms = {v["vmid"]: v for v in r.json()}

    # the mock fleet is present
    for vmid in (101, 105, 115, 120, 130, 140, 151):
        assert vmid in vms, f"vmid {vmid} missing from fleet"
    # templates are filtered out
    assert 9000 not in vms
    assert 9001 not in vms

    # protected flags follow HLIDSKJALF_PROTECTED_VMIDS=101,151
    assert vms[101]["protected"] is True
    assert vms[151]["protected"] is True
    assert vms[105]["protected"] is False

    jarvis = vms[105]
    assert jarvis["name"] == "vps-jarvis-prod"
    assert jarvis["kind"] == "qemu"
    assert jarvis["status"] == "running"
    assert jarvis["netin"] > 0 and jarvis["netout"] > 0
    assert vms[130]["kind"] == "lxc"


def test_vm_detail_ips_vlan_mac(auth_client):
    r = auth_client.get("/api/vms/105")
    assert r.status_code == 200
    d = r.json()
    assert d["vmid"] == 105
    assert d["name"] == "vps-jarvis-prod"
    assert d["status"] == "running"
    # IPs come from the guest agent (loopback filtered out)
    assert d["agent"] is True
    assert d["ips"] == ["10.0.20.15"]
    assert d["vlan"] == "20"
    assert d["mac"] == "BC:24:11:69:AA:01"  # 105 == 0x69
    assert d["bridge"] == "vmbr0"
    assert d["protected"] is False
    assert d["rescue"] is False
    assert d["config"]["cores"] == 8


def test_vm_detail_unknown_vmid_404(auth_client):
    assert auth_client.get("/api/vms/9999").status_code == 404
