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
```

## Local development (no Proxmox needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart

# 1. mock PVE on :18006
cd dev && ../.venv/bin/uvicorn mock_pve:app --port 18006 &

# 2. backend on :8787 (dev.env points it at the mock; login christina/devpass)
cd backend && set -a && source ../dev/dev.env && set +a \
  && ../.venv/bin/uvicorn hlidskjalf.main:app --port 8787 --reload &

# 3. frontend dev server on :5173, proxying /api and /ws to :8787
cd frontend && npm ci && npm run dev
```

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
