"""Unit tests for scripts/validate-proxmox.py::unmet_requirements.

Regression for a false WARN observed against real Proxmox VE 9.2.3: the
validator demanded `VM.Monitor` for guest-agent IP discovery, but PVE 9 split
guest-agent access out of VM.Monitor into the VM.GuestAgent.* family. The
documented role set (PVEVMAdmin, PVEDatastoreUser, PVEAuditor) grants
VM.GuestAgent.Audit — which is what `agent/network-get-interfaces` actually
needs on PVE 9 — yet the old single-privilege map reported "token lacks:
VM.Monitor" while the agent call was in fact authorized.

The script is not a package; import it by path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-proxmox.py"


@pytest.fixture(scope="module")
def validator():
    """Import the script as a module. Must not run argparse or touch the network
    (main() is guarded by `if __name__ == "__main__"`)."""
    spec = importlib.util.spec_from_file_location("validate_proxmox", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # The script's dataclasses resolve their (string) annotations by looking the
    # module up in sys.modules — register it before executing.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)


# The exact effective privileges on / observed for hlidskjalf@pve!panel
# (roles PVEVMAdmin,PVEDatastoreUser,PVEAuditor, privsep 0) on PVE 9.2.3.
PVE9_PRIVS = {
    "Datastore.AllocateSpace", "Datastore.Audit", "Mapping.Audit", "Pool.Audit",
    "SDN.Audit", "Sys.Audit", "VM.Allocate", "VM.Audit", "VM.Backup", "VM.Clone",
    "VM.Config.CDROM", "VM.Config.CPU", "VM.Config.Cloudinit", "VM.Config.Disk",
    "VM.Config.HWType", "VM.Config.Memory", "VM.Config.Network", "VM.Config.Options",
    "VM.Console", "VM.GuestAgent.Audit", "VM.GuestAgent.FileRead",
    "VM.GuestAgent.FileSystemMgmt", "VM.GuestAgent.FileWrite",
    "VM.GuestAgent.Unrestricted", "VM.Migrate", "VM.PowerMgmt", "VM.Replicate",
    "VM.Snapshot", "VM.Snapshot.Rollback",
}

# What the token needs to actually work: the above PLUS SDN.Use (role PVESDNUser).
# Without it PVE 9 refuses to attach a NIC to a bridge/VLAN, so every clone fails.
SUFFICIENT = PVE9_PRIVS | {"SDN.Use"}

# A PVE 8-style grant: VM.Monitor exists, no VM.GuestAgent.* family.
PVE8_PRIVS = (SUFFICIENT - {p for p in SUFFICIENT if p.startswith("VM.GuestAgent.")}) | {
    "VM.Monitor",
}


def test_pve9_role_set_without_sdn_user_cannot_provision(validator):
    """The role set we documented is NOT sufficient on PVE 9 — SDN.Use is missing.

    Proven the hard way on real hardware (2026-07-13): every clone died with
        Permission check failed (/sdn/zones/localnetwork/vmbr1/20, SDN.Use)
    because PVE 9 gates *attaching a NIC to a bridge/VLAN* behind SDN.Use, and
    PVEAuditor grants only SDN.Audit (read). The fix is the PVESDNUser role. The
    validator must now say so BEFORE anyone clicks "create VM".
    """
    unmet = validator.unmet_requirements(PVE9_PRIVS)
    assert [alts for alts, _ in unmet] == [frozenset({"SDN.Use"})]
    assert "PVESDNUser" in unmet[0][1]


def test_pve9_role_set_with_sdn_user_is_sufficient(validator):
    """PVEVMAdmin + PVEDatastoreUser + PVEAuditor + PVESDNUser — the real minimum."""
    assert validator.unmet_requirements(SUFFICIENT) == []


def test_pve8_vm_monitor_still_satisfies_the_agent_requirement(validator):
    """Older hosts gate the agent behind VM.Monitor; that must remain acceptable."""
    unmet = validator.unmet_requirements(PVE8_PRIVS)
    agent_unmet = [(alts, why) for alts, why in unmet if "guest-agent" in why]
    assert agent_unmet == []
    assert unmet == []


def test_missing_privilege_is_reported_with_its_reason(validator):
    unmet = validator.unmet_requirements(SUFFICIENT - {"VM.Console"})
    assert len(unmet) == 1
    alts, why = unmet[0]
    assert alts == frozenset({"VM.Console"})
    assert "noVNC console" in why


def test_neither_agent_privilege_reports_both_alternatives(validator):
    unmet = validator.unmet_requirements(SUFFICIENT - {"VM.GuestAgent.Audit"})
    assert len(unmet) == 1
    alts, why = unmet[0]
    assert alts == frozenset({"VM.GuestAgent.Audit", "VM.Monitor"})
    assert "QEMU guest-agent IP discovery" in why


def test_empty_privileges_leaves_every_requirement_unmet(validator):
    assert len(validator.unmet_requirements(set())) == len(validator.NEEDED_PRIVILEGES)
