"""Fleet list and VM detail against the mock PVE."""

import httpx


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

    vm = vms[105]
    assert vm["name"] == "vps-alpha"
    assert vm["kind"] == "qemu"
    assert vm["status"] == "running"
    assert vm["netin"] > 0 and vm["netout"] > 0
    assert vms[130]["kind"] == "lxc"


def test_vm_detail_ips_vlan_mac(auth_client):
    r = auth_client.get("/api/vms/105")
    assert r.status_code == 200
    d = r.json()
    assert d["vmid"] == 105
    assert d["name"] == "vps-alpha"
    assert d["status"] == "running"
    # IPs come from the guest agent (loopback filtered out)
    assert d["agent"] is True
    assert d["ips"] == ["192.168.20.15"]
    assert d["vlan"] == "20"
    assert d["mac"] == "BC:24:11:69:AA:01"  # 105 == 0x69
    assert d["bridge"] == "vmbr0"
    assert d["protected"] is False
    assert d["rescue"] is False
    assert d["config"]["cores"] == 8


def test_vm_detail_unknown_vmid_404(auth_client):
    assert auth_client.get("/api/vms/9999").status_code == 404


def test_mock_qemu_guests_report_disk_zero_like_real_pve(mock_pve_url):
    """Parity assertion with real PVE (validated on a 9.2.3 host, 2026-07-13).

    Real PVE reports disk=0 for QEMU guests in /cluster/resources — the
    hypervisor cannot see in-guest filesystem usage without asking the agent,
    and it doesn't. Every running QEMU guest on the real host reported disk=0.
    LXC containers DO report real, non-zero disk usage. The mock used to
    fabricate 45% of maxdisk for everything, so the suite proved
    self-consistency with a fiction.
    """
    r = httpx.get(f"{mock_pve_url}/api2/json/cluster/resources", timeout=5.0)
    assert r.status_code == 200
    by_vmid = {row["vmid"]: row for row in r.json()["data"]}

    qemu_running = [row for row in by_vmid.values()
                    if row["type"] == "qemu" and row["status"] == "running"]
    assert qemu_running, "mock fleet must contain running qemu guests"
    for row in qemu_running:
        assert row["disk"] == 0, f"qemu {row['vmid']} fabricates disk usage"
        assert row["maxdisk"] > 0

    lxc = by_vmid[130]
    assert lxc["type"] == "lxc" and lxc["status"] == "running"
    assert lxc["disk"] > 0, "running lxc should report real disk usage"

    # status/current must agree with /cluster/resources
    r = httpx.get(
        f"{mock_pve_url}/api2/json/nodes/pve/qemu/105/status/current", timeout=5.0
    )
    assert r.json()["data"]["disk"] == 0


def test_panel_passes_through_qemu_disk_zero(auth_client):
    """The fleet, detail and metrics endpoints must not choke on disk=0."""
    fleet = {v["vmid"]: v for v in auth_client.get("/api/vms").json()}
    assert fleet[105]["disk"] == 0
    assert fleet[105]["maxdisk"] == 200 << 30
    assert fleet[130]["disk"] > 0  # lxc keeps honest, non-zero usage

    r = auth_client.get("/api/vms/105")
    assert r.status_code == 200
    d = r.json()
    assert d["disk"] == 0
    assert d["maxdisk"] == 200 << 30

    # rrddata is consistent: real PVE reports 0 in the qemu disk series too
    r = auth_client.get("/api/vms/105/metrics?timeframe=hour&cf=AVERAGE")
    assert r.status_code == 200
    rows = r.json()
    assert rows
    assert all(row["disk"] == 0 for row in rows)
    assert all(row["maxdisk"] == 200 << 30 for row in rows)
