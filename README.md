# Hlidskjalf

> *Hliðskjálf* — Odin's high seat, from which he watches over all the realms.  
> **v0.4.1-alpha** · tested against a real Proxmox VE 9.2.3 host

A self-hosted, multi-user **Proxmox VE control panel**: fleet overview, live graphs,
per-VM bandwidth accounting with monthly charts and quotas, provisioning from cloud-init
templates, reinstall, SystemRescue boot, and a working console for both VMs and
containers — all through a **non-root, scoped PVE API token**, with the Proxmox TLS
certificate **pinned by SHA-256 fingerprint**.

Each regular user is scoped to exactly one VM (the VPS model); admins see the whole
fleet. FastAPI backend serving a React SPA — **one service, one port**.

---

## Quick start

The panel ships **unconfigured**. Start it, open it in a browser, and it serves a
**setup wizard**: point it at your Proxmox, paste an API token (validated with a live
call before anything is saved), create the admin account. No env file required.

```bash
docker compose up -d           # or: pip install -e backend && hlidskjalf
# open http://localhost:8787
```

### 1. Make a scoped Proxmox token

Never `root@pam`, never a password, and never `PVEAdmin`. Four narrow roles, each on the
path that needs it — **the wizard prints these commands for you**, generated from the
token id you type:

```bash
pveum user add hlidskjalf@pve

pveum acl modify /vms       --users hlidskjalf@pve --roles PVEVMAdmin       # guests
pveum acl modify /storage   --users hlidskjalf@pve --roles PVEDatastoreUser # clone disks
pveum acl modify /          --users hlidskjalf@pve --roles PVEAuditor       # GET /nodes, tasks
pveum acl modify /sdn/zones --users hlidskjalf@pve --roles PVESDNUser       # NIC → bridge/VLAN

pveum user token add hlidskjalf@pve panel --privsep 0   # prints the secret ONCE
```

Three traps, each producing a token that authenticates and then fails everything:

- **`--privsep 0` is mandatory** — otherwise the token carries its own empty ACL.
- **`PVEAuditor` alone is not enough** — no console, no power, no provisioning.
- **`PVESDNUser` is not optional on Proxmox 9** — attaching a NIC to a bridge/VLAN needs
  `SDN.Use`, and without it *every* clone fails with `Permission check failed (…, SDN.Use)`.

The resulting token cannot reboot the host, change permissions, create users,
reconfigure storage, or alter SDN zones.

### 2. Get the certificate fingerprint

The panel pins the Proxmox cert rather than trusting whatever answers on the wire:

```bash
openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
```

### 3. Validate before it touches anything

```bash
python scripts/validate-proxmox.py --host <pve-host> --node <node> \
    --token-id 'hlidskjalf@pve!panel' --fingerprint AA:BB:...:FF
```

Read-only by default (it mutates nothing without `--allow-writes --vmid <≥900>`). It
checks every assumption the panel is built on — token privileges, cert pinning, `/nodes`
with a scoped token, `/cluster/resources` shape, UPID parsing, rrddata, guest agent,
console websocket — and prints PASS/FAIL with the observed value and the file each
failure breaks.

Full walkthrough: **[docs/setup.md](docs/setup.md)**.

Prefer to configure declaratively? Set the environment variables instead
(`hlidskjalf.env.example`) and the wizard never appears — **env always wins** over
anything the wizard stored, so secrets can live in agenix/sops/systemd-creds. Every
secret also takes a `*_FILE` twin, because a secret manager hands you a file.

---

## What's in it

| | |
|---|---|
| **Fleet** | every guest on the node, live status, quick power actions |
| **VM detail** | overview, graphs, console, rescue, tasks — scoped per user |
| **Console** | **noVNC** for VMs, **xterm.js** for containers (Proxmox serves no working VNC for LXC — its RFB handshake hangs at ClientInit, so containers go through `termproxy`) |
| **Provision** | clone a cloud-init template, set cores/RAM/disk/VLAN/IP/SSH keys |
| **Bandwidth** | daily/monthly per-VM accounting with quotas |
| **Rescue** | boot a SystemRescue ISO, then restore the original boot order |
| **Users** | admins manage tenants; each tenant sees exactly one VM |
| **Settings** | VLANs → gateways, disk storage, network bridge — from what the node actually reports |
| **Updates** | the panel notices when a new commit lands on GitHub, and can apply it (opt-in) |
| **Profile** | change your own password (invalidates every other session) |

---

## Starting it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart

./scripts/dev.sh --mock      # no Proxmox needed: starts dev/mock_pve.py too
./scripts/dev.sh             # against a real Proxmox, using dev/dev.env
./scripts/dev.sh --reload    # + restart the backend on every save
./scripts/dev.sh --vite      # + Vite on :5173 with hot reload (open THAT url)
```

The panel is then on <http://localhost:8787>. First run builds the SPA automatically.
The script warns if `HLIDSKJALF_PROTECTED_VMIDS` is empty, because then **nothing** is
safe from destroy — including the machine the panel runs on.

`scripts/dev.sh` is a **development** launcher. Production is Docker
(`docs/docker.md`), the NixOS module (`nix/module.nix`), or the `hlidskjalf` console
script under systemd (`docs/dev-against-real-proxmox.md` §7).

<details>
<summary>The same thing by hand, if you'd rather</summary>

```bash
cd dev     && ../.venv/bin/uvicorn mock_pve:app --port 18006 &     # optional mock
cd backend && set -a && source ../dev/dev.env && set +a \
           && ../.venv/bin/uvicorn hlidskjalf.main:app --port 8787 --reload
cd frontend && npm ci && npm run dev        # :5173, proxies /api and /ws to :8787
```
</details>

---

## Updating

**Settings → Updates** compares the commit the panel is running with the tip of `main`
on GitHub and tells you how far behind you are, with the commit list. The check is
**fail-soft** (no network → no update offered, no error, no nag) and sends nothing
identifying — an anonymous GET of a public repo. Disable with
`HLIDSKJALF_UPDATE_CHECK_ENABLED=false`.

How you *apply* it depends on how you installed — and the panel does not pretend
otherwise:

| install | how it updates |
|---|---|
| **Docker** | `docker compose pull && docker compose up -d` |
| **NixOS** | update the flake input, then `nixos-rebuild switch` |
| **git + venv** | the panel can apply it itself — **opt-in** |

A container cannot replace its own image and a Nix system updates from its flake, so for
those the panel shows the command instead of pretending. For a git install:

```bash
HLIDSKJALF_ALLOW_SELF_UPDATE=true
```

**Off by default, and it cannot be turned on from inside the panel** — it is remote code
execution by design: it fetches code from GitHub and runs it. Even enabled, applying an
update requires an admin session, CSRF, a typed confirmation, a **clean working tree**,
an `origin` matching the configured repo, and a **fast-forward to exactly the commit you
were shown**. It backs up the database first, **proves the new code imports before
restarting**, and **rolls back** if anything fails. Every attempt — including every
refusal — is audited.

---

## Real-hardware status

**v0.4.1-alpha has been run against a real Proxmox VE 9.2.3 host.** The first-run
wizard, fleet, node, graphs and both consoles work there. That run found five defects
that months of green tests never did — see [`CHANGELOG.md`](CHANGELOG.md) and
[`handoff.md`](handoff.md).

**The write paths are still unproven**: nothing has been provisioned, reinstalled,
rescued or destroyed through the panel on real hardware yet.

All 242 backend tests pass — against `dev/mock_pve.py`, **a mock we wrote ourselves**.
Green tests prove self-consistency, not correctness. That mock has been caught lying
three times (8-field UPIDs where real PVE emits 9; fabricated QEMU disk usage where real
PVE reports 0; one echo websocket that made a container's console look identical to a
VM's). Assume there are more, and run the validator before trusting it with a Proxmox
you care about:
**[docs/real-hardware-validation.md](docs/real-hardware-validation.md)** ·
**[docs/dev-against-real-proxmox.md](docs/dev-against-real-proxmox.md)**.

---

## Safety rails

- **`HLIDSKJALF_PROTECTED_VMIDS`** — destroy/reinstall/stop/reset are refused
  **server-side** for these guests; shutdown/reboot stay allowed. It defaults to
  **empty**, so *nothing* is protected until you set it. Put the panel's own host in it.
- Destroy and reinstall require typing the **exact guest name**, checked server-side.
- **Per-VM authorisation on every route** — a regular user sees exactly one VM, and task
  status is scoped to the guest the UPID belongs to.
- Sessions are signed cookies **bound to the password they were issued under**; changing
  a password invalidates every older session. CSRF on every mutation. Logout revokes.
- The PVE token is **encrypted at rest** and never returned by any API.
- Every NIC the panel writes gets `firewall=0` — a VLAN-tagged NIC on a firewall bridge
  silently drops traffic otherwise.
- **Never expose the panel publicly.** It is designed for a LAN, behind a reverse proxy.

---

## Layout

```
backend/    Python package `hlidskjalf` (FastAPI, PVE client, bandwidth accumulator)
frontend/   Vite + React + TS + Tailwind SPA (served by the backend as static files)
nix/        package.nix (frontend+backend build) and module.nix (services.hlidskjalf)
dev/        mock_pve.py — a fake PVE API, so everything runs without a real Proxmox
docs/       setup.md, docker.md, real-hardware-validation.md, dev-against-real-proxmox.md
scripts/    dev.sh (launcher) · validate-proxmox.py (check assumptions against a REAL host)
```

`plan.md` is the design source of truth. `handoff.md` is what's done and what's next.

## Screenshots

**[docs/screenshots/](docs/screenshots/)** — latest gallery is
[v0.3.6-alpha](docs/screenshots/v0.3.6-alpha/README.md) (includes the setup wizard);
[v0.3.5-alpha](docs/screenshots/v0.3.5-alpha/README.md) covers the design system. All
captured against the development mock; the v0.4 pages (Settings, Updates, Profile, the
container terminal) are not yet in the gallery.

## Bandwidth accounting — known limits

Proxmox keeps no per-VM traffic history, so the panel samples the cumulative
`netin`/`netout` counters every 60 s and books deltas into sqlite (UTC days). Counter
resets on guest restart are handled; traffic while the panel itself is down is simply
unaccounted. The numbers are for capacity awareness, **not billing**.

## Known limitations

- **Single Proxmox node** — a cluster shows only the configured node.
- Provisioning is **QEMU-only** (containers list, power and console fine; LXC *create*
  is not implemented).
- Provisioning always picks the next free VMID; you cannot choose one yet.
- The switch faceplate is hardcoded to a 48-port Arista DCS-7050TX-48 and does not
  render from what the switch reports. The Switch page is optional — leave
  `switch_host` unset and it disappears.
