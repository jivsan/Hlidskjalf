# Hlidskjalf

> *Hliðskjálf* — Odin's high seat, from which he watches over all the realms.

A self-hosted, single-admin VPS control panel for the **hella** Proxmox host:
fleet overview, live graphs, per-VM bandwidth accounting with monthly charts and
quotas, provisioning from cloud-init templates, reinstall, SystemRescue boot,
and a noVNC console — all through a **non-root, scoped PVE API token** with the
API TLS cert pinned by SHA-256 fingerprint.

FastAPI backend serving a React SPA (Tokyo Night, all-mono), one service, one
port. See `plan.md` for the full design and `docs/bootstrap.md` for the one-time
setup on hella.

## Screenshots

The screenshot gallery is maintained in its own versioned folder for easy tracking across releases:

**📁 [docs/screenshots/](docs/screenshots/)**

- **Current:** [v0.2-alpha](docs/screenshots/v0.2-alpha/README.md)
- Images are taken against the mock PVE (`dev/mock_pve.py`).

See the versioned directory for the full gallery with descriptions.

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
