# Manual bootstrap on the Proxmox host (one-time, needs root on the PVE shell / UI)

The panel itself never needs root. These steps create the scoped service
account, the cloud-init template(s), and the rescue ISO it works with.

## 1. PVE user, role scoping, API token (no root)

```bash
# On pve (Proxmox shell)
pveum user add hlidskjalf@pve --comment "hlidskjalf panel service account"

# Read-only audit of everything (stats, node info, storage listing)
pveum aclmod / --users hlidskjalf@pve --roles PVEAuditor

# Full VM lifecycle (includes VM.Allocate, VM.Clone, VM.Config.*, VM.PowerMgmt,
# VM.Console, VM.Monitor, VM.Snapshot, VM.Audit) — scoped to /vms, NOT /
pveum aclmod /vms --users hlidskjalf@pve --roles PVEVMAdmin

# Allow allocating disk space on the storage used for VM disks + reading ISOs.
# Adjust storage IDs to reality (check: pvesm status)
pveum aclmod /storage/local-lvm --users hlidskjalf@pve --roles PVEDatastoreUser
pveum aclmod /storage/local     --users hlidskjalf@pve --roles PVEDatastoreUser

# PVE 8+: using a bridge from the API requires SDN.Use on that bridge
pveum aclmod /sdn/zones/localnetwork/vmbr0 --users hlidskjalf@pve --roles PVESDNUser

# Token WITH privilege separation. Privsep means the token's effective perms are
# the INTERSECTION of user ACLs and token ACLs — so repeat the ACLs for the token:
pveum user token add hlidskjalf@pve panel --privsep 1
pveum aclmod /      --tokens 'hlidskjalf@pve!panel' --roles PVEAuditor
pveum aclmod /vms   --tokens 'hlidskjalf@pve!panel' --roles PVEVMAdmin
pveum aclmod /storage/local-lvm --tokens 'hlidskjalf@pve!panel' --roles PVEDatastoreUser
pveum aclmod /storage/local     --tokens 'hlidskjalf@pve!panel' --roles PVEDatastoreUser
pveum aclmod /sdn/zones/localnetwork/vmbr0 --tokens 'hlidskjalf@pve!panel' --roles PVESDNUser
```

> ⚠️ `pveum user token add` prints the secret **once**. It goes into the env file
> on panel-host (§4), never into git.

Pin the API TLS cert (self-signed) by SHA-256 fingerprint:

```bash
openssl s_client -connect <pve-host>:8006 </dev/null 2>/dev/null \
  | openssl x509 -fingerprint -sha256 -noout
```

The `XX:XX:...` value goes into `services.hlidskjalf.settings.pveFingerprint`.
The panel refuses to start over https without it and aborts any connection whose
cert digest differs.

## 2. Cloud-init template (Debian 13, VMID 9000)

```bash
cd /var/lib/vz/template   # or wherever there's space
wget https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2

qm create 9000 --name debian13-template --memory 2048 --cores 2 \
  --net0 virtio,bridge=vmbr0,tag=20,firewall=0 \
  --scsihw virtio-scsi-single --agent enabled=1 --ostype l26
qm set 9000 --scsi0 local-lvm:0,import-from=/var/lib/vz/template/debian-13-genericcloud-amd64.qcow2
qm set 9000 --ide2 local-lvm:cloudinit --boot order=scsi0 --serial0 socket --vga serial0
qm template 9000
```

> ⚠️ **`firewall=0` is mandatory** on every NIC with a VLAN tag —
> `firewall=1` silently breaks tag propagation through the firewall bridge
> (documented fleet-wide bug). The panel hardcodes `firewall=0` on every NIC it
> ever creates or edits.

Optionally repeat for Ubuntu 24.04 (VMID 9001). Any QEMU VM with `template=1`
is auto-discovered by the panel — no per-template config needed.

## 3. Rescue ISO

```bash
# Into the ISO storage ('local' by default)
cd /var/lib/vz/template/iso
wget https://fastly-cdn.system-rescue.org/releases/latest/systemrescue-*-amd64.iso
```

The ISO volid (e.g. `local:iso/systemrescue-12.01-amd64.iso`) goes into
`services.hlidskjalf.settings.rescueIso`.

## 4. Secrets env file on panel-host

Root-owned `0600`, e.g. `/etc/hlidskjalf/env`, referenced by
`services.hlidskjalf.environmentFile` (move to sops-nix/agenix later):

```
HLIDSKJALF_PVE_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
HLIDSKJALF_ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
HLIDSKJALF_SESSION_SECRET=<openssl rand -hex 32>
```

Generate the password hash (paste the password at the prompt, nothing lands in
shell history):

```bash
nix run nixpkgs#python312 -- -c '
import getpass
from argon2 import PasswordHasher
print(PasswordHasher().hash(getpass.getpass("panel password: ")))'
```

(If argon2-cffi is missing in that interpreter:
`nix shell nixpkgs#python312Packages.argon2-cffi nixpkgs#python312` first.)

## 5. Traefik + DNS on panel-host (dotfiles repo)

```nix
services.traefik.dynamicConfigOptions.http = {
  routers.hlidskjalf = {
    rule = "Host(`hlidskjalf.oryxserver.org`)";
    entryPoints = [ "websecure" ];
    service = "hlidskjalf";
    tls.certResolver = "cloudflare";
  };
  services.hlidskjalf.loadBalancer.servers = [ { url = "http://127.0.0.1:8787"; } ];
};
```

WebSockets (the noVNC console) pass through Traefik untouched. DNS:
`hlidskjalf.oryxserver.org` → 192.168.20.17, same as grafana.oryxserver.org.
LAN-only — do not add a public ingress.
