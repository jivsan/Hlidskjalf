# Hlidskjalf

> *Hliðskjálf* — Odin's high seat, from which he watches over all the realms.  
> v0.3.6-alpha

A self-hosted, multi-user **Proxmox VE control panel**: fleet overview, live
graphs, per-VM bandwidth accounting with monthly charts and quotas, provisioning
from cloud-init templates, reinstall, SystemRescue boot, and a noVNC console —
all through a **non-root, scoped PVE API token**, with the API TLS certificate
pinned by SHA-256 fingerprint.

Each regular user is scoped to exactly one VM (the VPS model); admins see the
whole fleet. FastAPI backend serving a React SPA — one service, one port.

## Getting started

The panel ships **unconfigured**. Start it and open it in a browser: it serves a
**setup wizard** where you point it at your Proxmox, paste an API token (checked
with a live call before anything is saved), create the admin account, and
optionally create a first user. No env file required.

```bash
docker compose up -d      # or: pip install -e backend && hlidskjalf
# open http://localhost:8787
```

Full walkthrough — including how to mint the scoped Proxmox token, and exactly
where the secret is stored — in **[docs/setup.md](docs/setup.md)**.

Prefer to configure everything declaratively? Set the environment variables
instead (`hlidskjalf.env.example`) and the wizard never appears — env always wins
over anything the wizard stored, so secrets can live in agenix/sops/systemd-creds.
See `plan.md` for the full design and `docs/bootstrap.md` for the Proxmox-side setup.

## Screenshots

```
  [ CYAN / PINK SERVER ROOM FEEDS ]
  ╔════════════════════════════════════════════════════╗
  ║  ACCESS: docs/screenshots/  |  CURRENT: v0.3.6-alpha ║
  ╚════════════════════════════════════════════════════╝
```

Enter the rack: **[docs/screenshots/](docs/screenshots/)**  

**Current (v0.3.6-alpha - first-run setup wizard, security audit, no hardcoded host, Prometheus, −71% bundle):**  
[v0.3.6-alpha/README.md](docs/screenshots/v0.3.6-alpha/README.md)  
(Real captured screenshots after merges — includes the setup wizard)

**Previous (v0.3.5-alpha design system):** [v0.3.5-alpha/README.md](docs/screenshots/v0.3.5-alpha/README.md)

**Previous baseline:** [v0.2-alpha/README.md](docs/screenshots/v0.2-alpha/README.md)

All shots captured against the development mock PVE (`dev/mock_pve.py`).

> The control surface glows in the dark. Welcome to the server room.

## Layout

```
backend/    Python package `hlidskjalf` (FastAPI, PVE client, bandwidth accumulator)
frontend/   Vite + React + TS + Tailwind SPA (built at Nix build time)
nix/        package.nix (frontend+backend build) and module.nix (services.hlidskjalf)
dev/        mock_pve.py — fake PVE API so everything runs without touching hella
docs/       bootstrap.md — manual one-time steps on hella (token, template, ISO)
scripts/    validate-proxmox.py — check the panel's assumptions against a REAL host
```

## Real-hardware validation

⚠️ **The panel has never been run against a real Proxmox host.** All 163 tests pass —
against `dev/mock_pve.py`, a mock we wrote ourselves, so they prove self-consistency,
not correctness.

Before pointing this at a Proxmox you care about, run the read-only validator:

```bash
python scripts/validate-proxmox.py --host <pve-host> --node <node> \
    --token-id 'hlidskjalf@pve!panel' --fingerprint AA:BB:...:FF
```

It checks each assumption the panel is built on (token auth, cert pinning, `/nodes` with
a scoped token, `/cluster/resources` shape, UPID parsing, rrddata, guest agent, console
websocket) and prints PASS/FAIL with the observed value and the file each failure breaks.
It is **read-only by default** and mutates nothing without `--allow-writes --vmid <>=900>`.

Full instructions, the token setup, and the manual checklist (open the console and type
in it; power-cycle a scratch VM; confirm a protected VMID refuses destroy):
**[docs/real-hardware-validation.md](docs/real-hardware-validation.md)**.

Setting up a scratch Debian VM to develop against your real Proxmox (fast reload loop,
safety rails, and what to expect to break first):
**[docs/dev-against-real-proxmox.md](docs/dev-against-real-proxmox.md)**.

## Starting it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart

./scripts/dev.sh --mock      # no Proxmox needed: starts dev/mock_pve.py too
./scripts/dev.sh             # against a real Proxmox, using dev/dev.env
./scripts/dev.sh --reload    # + restart the backend on every save
./scripts/dev.sh --vite      # + Vite on :5173 with hot reload (open THAT url)
```

The panel is then on <http://localhost:8787> — one service, one port; the backend
serves the built SPA. First run builds the SPA automatically. The script warns if
`HLIDSKJALF_PROTECTED_VMIDS` is empty, because then *nothing* is safe from destroy —
including the machine the panel runs on.

`scripts/dev.sh` is a **development** launcher. It is not how the panel runs in
production: Docker has its own entrypoint (`docs/docker.md`), the NixOS module runs
it under systemd (`nix/module.nix`), and a plain install runs the `hlidskjalf`
console script — or `uvicorn hlidskjalf.main:app` — under a systemd unit
(`docs/dev-against-real-proxmox.md` §7). What all of them share is the environment:
every setting is an `HLIDSKJALF_*` env var, and every secret also takes a `*_FILE`
twin so a secret manager can hand it a file instead.

<details>
<summary>The same thing by hand, if you'd rather</summary>

```bash
cd dev     && ../.venv/bin/uvicorn mock_pve:app --port 18006 &     # optional mock
cd backend && set -a && source ../dev/dev.env && set +a \
           && ../.venv/bin/uvicorn hlidskjalf.main:app --port 8787 --reload
cd frontend && npm ci && npm run dev        # :5173, proxies /api and /ws to :8787
```
</details>

## Deployment (heimdall, NixOS)

Flake input + module (see `docs/bootstrap.md` §4–5 for secrets and Traefik):

```nix
inputs.hlidskjalf.url = "github:jivsan/Hlidskjalf";

# hosts/heimdall/...
imports = [ inputs.hlidskjalf.nixosModules.hlidskjalf ];
services.hlidskjalf = {
  enable = true;
  environmentFile = "/etc/hlidskjalf/env";
  settings = {
    pveFingerprint = "AA:BB:...";          # docs/bootstrap.md §1
    rescueIso = "local:iso/systemrescue-12.01-amd64.iso";
    protectedVmids = [ 101 151 ];          # heimdall itself, hermes-agent, HAOS, PBS…
    bandwidthQuotas = { "115" = 500; };    # GB/month, display-only
  };
};
```

Before first `nix build`: set the real `npmDepsHash` in `nix/package.nix`
(build once, copy the hash from the error).

## Bandwidth accounting — known limits

PVE keeps no per-VM traffic history, so the panel samples the cumulative
netin/netout counters every 60 s and books deltas into sqlite (UTC days).
Counter resets on VM restart are handled; traffic while the panel itself is
down is simply unaccounted. Numbers are for capacity awareness, not billing.

## Safety rails

- `protectedVmids`: destroy/reinstall/stop/reset are refused **server-side**;
  shutdown/reboot stay allowed.
- Destroy and reinstall require typing the exact VM name (checked server-side).
- Every NIC the panel writes gets `firewall=0` — VLAN tags break through the
  firewall bridge on hella otherwise (fleet-wide bug).
- Session cookie is HttpOnly + SameSite=Strict; all mutations additionally
  require the `X-Hlidskjalf-CSRF` header.
- Never expose the panel publicly; it is designed for LAN + Traefik.
