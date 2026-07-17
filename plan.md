# Hlidskjalf — self-hosted VPS panel for a Proxmox host

> A NorwayVPS/"flux"-style control panel, self-hosted on its own VM, talking to the
> Proxmox API on the node with a **non-root, scoped API token**.
> Norse naming: *Hliðskjálf*, Odin's high seat from which he watches over all the realms —
> this is the high seat overlooking the node. `hlidskjalf` is used consistently below.

---

## 0. Status (2026-07) — read this first

**This file was the founding plan; the panel has since shipped.** M0–M5 are done,
the read-only surface is validated against real hardware (PVE 9.2.3), and the
living documents have moved:

- `handoff.md` — what's done / what's next, kept current every session.
- `docs/design/v0.5.0-cyberpunk.md` — the current visual language (supersedes
  §5's Tokyo Night section below and `docs/design/v0.3.5-design-system.md`).
- `docs/bootstrap.md` / `docs/setup.md` — provisioning the token/template,
  now mostly handled by the in-app setup wizard.
- `CHANGELOG.md` — release history.

What shipped beyond this plan: **multi-user tenancy** (admins see the fleet,
regular users are scoped to exactly one VM — the §1 non-goal became the VPS
model), a first-run setup wizard, the switch faceplate page, update detection,
and tenant access via tunnels (Pangolin/Newt — see `docs/`).

What is still true below: the architecture (§2), the PVE API mapping (§4), the
bandwidth accumulator design (§4.1), and the safety rails. Where the text below
disagrees with `handoff.md`/`CLAUDE.md`, **the newer docs win.** The roadmap
now: **M6 — the v0.5.0 cyberpunk design pass** (in progress), then **Phase 3 —
real-hardware validation of the write paths** (provision/reinstall/rescue/
destroy on scratch VMIDs ≥ 900).

## 1. Goal

Clone the essential UX of a commercial Proxmox VPS panel for a single-admin homelab:

- **Overview page per VM**: status, IP, disk usage bar, bandwidth counters, live CPU + network sparkline, action buttons (start / shutdown / reboot / stop / console).
- **Graphs page**: CPU %, RAM, disk usage, disk I/O, network — with a date-range picker.
- **Provisioning**: create a VM by cloning a cloud-init template (name, cores, RAM, disk size, VLAN, static IP, SSH key), and destroy VMs.
- **Rescue mode**: boot any VM from a SystemRescue ISO and back.
- **Reinstall**: wipe a VM and re-clone it from its template, keeping VMID/MAC/IP.
- **noVNC console** in the browser.
- **Per-VM bandwidth accounting**: GB in/out per day and per month for every VM, with a
  date-range view, a Jan–Dec monthly bar chart, and optional per-VM monthly quotas
  showing utilization % (like the "6.16 / 8000 GB" card in the reference panel).
- Fleet list page showing all VMs/LXCs on the node at a glance.

Explicit non-goals: multiple Proxmox nodes (a cluster shows just the one configured
node), LXC provisioning (LXC list/power/console work; create does not), billing.
Multi-user shipped: admins see the fleet, tenants are scoped to exactly one VM.

## 2. Architecture

```
dev box ──git push──▶ github.com/jivsan/Hlidskjalf
                            │ flake input
                            ▼
panel VM (NixOS, on the Proxmox node)
  ├─ hlidskjalf.service (systemd, DynamicUser)
  │    FastAPI backend ── serves built React SPA as static files
  │    │
  │    ├── REST → https://<pve-host>:8006/api2/json  (PVE API, token auth)
  │    └── WS proxy → wss://<pve-host>:8006 ... vncwebsocket  (console)
  └─ reverse proxy ──▶ https://<panel-domain>  (LAN, or tunnel for tenants)
```

- **Backend**: Python 3.12, FastAPI + uvicorn + httpx (async) + `websockets` for the VNC proxy.
  No proxmoxer — the API surface used is small, a thin typed client in `pve.py` is cleaner.
- **Frontend**: React 18 + TypeScript + Vite + Tailwind, Recharts for graphs,
  `@novnc/novnc` for the console. Built at Nix build time, served by the backend
  (single service, single port, no CORS).
- **Repo**: new repo `jivsan/hlidskjalf`. Flake exports `packages.x86_64-linux.hlidskjalf`
  and `nixosModules.hlidskjalf`. The dotfiles repo adds it as a flake input and enables
  the module in `hosts/panel-host/`.

## 3. Manual bootstrap

**Now handled by the in-app setup wizard** (first run, unauthenticated only until
the first user exists) — it generates the exact `pveum` commands from the token id
you type, including the four narrow roles and the mandatory `--privsep 0`. The
canonical text lives in `docs/bootstrap.md` / `docs/setup.md`; the short version:

- scoped non-root user `hlidskjalf@pve`, roles `PVEVMAdmin` on `/vms`,
  `PVEDatastoreUser` on `/storage`, `PVEAuditor` on `/`, **`PVESDNUser` on
  `/sdn/zones` (not optional on PVE 9)** — never `root@pam`, never `PVEAdmin`.
- token with `--privsep 0` (privilege separation OFF — otherwise the token gets
  its own empty ACL and can do nothing). The secret prints once → env file,
  never git.
- PVE TLS cert pinned by SHA-256 fingerprint (`openssl x509 -in
  /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256`).
- a Debian cloud-init template (any VMID with `template=1` is auto-discovered)
  with `firewall=0` on every tagged NIC — still a hard rule below — and a
  SystemRescue ISO on the ISO storage.

## 4. Backend spec

`hlidskjalf/` Python package. Layout:

```
backend/
  hlidskjalf/
    main.py        # FastAPI app, static file mount, lifespan
    config.py      # pydantic-settings, all from env (see §7)
    auth.py        # single-user login, argon2 verify, signed session cookie
    pve.py         # async PVE client: httpx.AsyncClient, token header, fingerprint pin
    accumulator.py # background task: per-VM bandwidth accounting into sqlite (see §4.1)
    db.py          # sqlite (aiosqlite) in StateDirectory: bandwidth, rescue stash, sessions
    routes/
      vms.py       # list/detail/status actions
      metrics.py   # rrddata passthrough + shaping
      bandwidth.py # daily/monthly per-VM traffic accounting queries
      provision.py # clone/destroy/reinstall
      rescue.py    # rescue enter/exit
      console.py   # vncproxy ticket + websocket proxy
    datasources/
      base.py      # MetricsSource protocol (get_series(vmid, metric, timeframe))
      rrd.py       # RRDSource — v1 implementation
      prometheus.py# stub with TODO — Phase 2 (panel-host Prometheus / pve-exporter)
```

PVE auth header: `Authorization: PVEAPIToken=hlidskjalf@pve!panel=<secret>`.
TLS: verify against pinned SHA-256 fingerprint (custom httpx transport), not `verify=False`.

### Endpoints

| Method | Path | Maps to PVE | Notes |
|---|---|---|---|
| POST | `/api/login` | — | argon2 verify, sets HttpOnly SameSite=Strict cookie |
| GET | `/api/vms` | `GET /cluster/resources?type=vm` | list incl. LXC, status, cpu, mem, uptime, tags |
| GET | `/api/vms/{vmid}` | `GET /nodes/pve/qemu/{vmid}/status/current` + `.../config` + agent `network-get-interfaces` | detail: IPs (via guest agent when running), disk bar, netin/netout counters |
| POST | `/api/vms/{vmid}/status/{action}` | `.../status/{start\|shutdown\|reboot\|stop\|reset}` | returns UPID; poll `/nodes/pve/tasks/{upid}/status` |
| GET | `/api/vms/{vmid}/metrics?timeframe=hour\|day\|week\|month&cf=AVERAGE` | `.../rrddata` | via MetricsSource; shape into `{t, cpu, mem, maxmem, disk, diskread, diskwrite, netin, netout}[]` |
| GET | `/api/node/metrics` | `/nodes/pve/rrddata` | host graphs for a node page |
| GET | `/api/vms/{vmid}/bandwidth?from=YYYY-MM-DD&to=YYYY-MM-DD` | panel sqlite | daily `{date, bytes_in, bytes_out}[]` + totals + quota/utilization if configured |
| GET | `/api/vms/{vmid}/bandwidth/monthly?year=2026` | panel sqlite | 12 rows `{month, bytes_in, bytes_out}` for the Jan–Dec bar chart |
| GET | `/api/bandwidth/summary?month=2026-07` | panel sqlite | all VMs, current-month totals — fleet column + top-talkers |
| GET | `/api/templates` | `/cluster/resources` filtered `template==1` | |
| POST | `/api/vms` | `POST .../qemu/{tpl}/clone` then `PUT .../config` then `PUT .../resize` | body: `{name, template_vmid, cores, memory_mb, disk_gb, vlan (20\|30\|50), ip_cidr, gateway, ssh_keys, start}` |
| POST | `/api/vms/{vmid}/reinstall` | destroy + clone (same vmid, same net0 MAC) | body: `{template_vmid, confirm_name}` |
| DELETE | `/api/vms/{vmid}` | `DELETE .../qemu/{vmid}?purge=1&destroy-unreferenced-disks=1` | body: `{confirm_name}` — must equal VM name |
| POST | `/api/vms/{vmid}/rescue` | `PUT config` ide2=rescue ISO, `boot=order=ide2`, reboot | stores original boot order in VM `description` JSON block or panel-side sqlite |
| DELETE | `/api/vms/{vmid}/rescue` | restore boot order, detach ISO, reboot | |
| GET | `/api/vms/{vmid}/console` | `POST .../vncproxy` (websocket=1) | returns local WS path + one-time token |
| WS | `/ws/console/{vmid}` | `wss://pve:8006/.../vncwebsocket?port=..&vncticket=..` | bidirectional byte pump; auth via session cookie |
| GET | `/api/tasks/recent` | `/nodes/pve/tasks?limit=50` | "Tasks and Logs" tab |

### 4.1 Bandwidth accounting (the accumulator)

PVE has no per-VM traffic history — only live cumulative `netin`/`netout` byte counters
(reset on every VM stop/start) and rrddata *rates*. The panel therefore does its own
bookkeeping, exactly like commercial VPS panels do:

- Background task (started in FastAPI lifespan), every **60 s**:
  1. `GET /cluster/resources?type=vm` — one call returns `netin`/`netout` for every
     VM and LXC.
  2. For each vmid, compute delta vs the last sample kept in memory:
     `delta = cur - prev if cur >= prev else cur` — the `else cur` branch is the
     **counter-reset rule** (VM rebooted/restored; counter restarted from 0, so
     everything since restart counts). Skip the very first sample after panel start
     (no baseline).
  3. `UPSERT` into sqlite: `bandwidth(vmid INTEGER, date TEXT, bytes_in INTEGER,
     bytes_out INTEGER, PRIMARY KEY (vmid, date))`, adding deltas to today's row
     (UTC dates, matching the reference panel's "all times are UTC").
- Missed intervals (panel down, panel-host reboot) are simply unaccounted — acceptable
  for a homelab; note it in the README. Worst case error = traffic during downtime.
- Persist the last-seen counters to sqlite every cycle too, so a panel restart
  doesn't double-count or lose the baseline (reload them on startup; if the VM
  restarted while the panel was down, the reset rule still catches it).
- Retention: keep daily rows forever (a few KB/year per VM); no pruning needed.
- **Quotas (optional, display-only)**: `bandwidthQuotas` maps vmid → GB/month
  (e.g. the VPS-facing VMs). UI shows Limit / Utilized / Utilization % card;
  ≥ 80% renders the bar amber, ≥ 100% pink. No enforcement — this is a homelab,
  nothing gets suspended.
- Nice-to-have (not required for acceptance): on first run, seed the current month
  approximately by integrating rrddata `netin`/`netout` rates over the `month`
  timeframe so the charts aren't empty on day one.

### Provisioning flow (POST /api/vms)

1. Reject if name collides or protected list touched.
2. `POST /nodes/pve/qemu/{template_vmid}/clone` `{newid: next_free_id ≥ 200, name, full: 1, storage}` → wait UPID.
3. `PUT config`: `cores`, `memory`, `net0: virtio,bridge=vmbr0,tag={vlan},firewall=0`,
   `ipconfig0: ip={ip_cidr},gw={gateway}`, `sshkeys` (url-encoded), `ciuser=admin`,
   `agent: enabled=1`, `onboot: 1`.
4. `PUT .../resize` `{disk: scsi0, size: {disk_gb}G}` if larger than template.
5. Optionally start. Return `{vmid, upids[]}` — frontend shows task progress.

### Reinstall flow

1. Require `confirm_name` == VM name, VMID not in protected list.
2. Read current config → save `net0` MAC, vlan tag, ipconfig0, cores, memory.
3. Stop (if running, `stop` not `shutdown`), destroy with purge.
4. Clone template to the **same VMID**, reapply saved MAC + net config
   (`net0=virtio={mac},bridge=vmbr0,tag={tag},firewall=0`), cloud-init, resize, start.

### Rescue flow

Enter: read `boot` from config → persist original → `PUT config`
`{ide2: "<rescue_iso>,media=cdrom", boot: "order=ide2"}` → `reboot` (or `stop`+`start` if
agentless). Exit: restore `boot`, set `ide2: none,media=cdrom` (templates keep their
cloudinit on a different slot — verify per-VM which ide slot is free; prefer `ide0`
for rescue if `ide2` is cloudinit), reboot. UI shows a persistent amber "RESCUE MODE"
banner on any VM whose boot order targets the rescue ISO.

### Safety rails (non-negotiable)

- `HLIDSKJALF_PROTECTED_VMIDS` (env, comma-sep): destroy/reinstall/stop/reset are **refused
  server-side** for these. **The default is EMPTY — nothing is protected.** Set it to the
  VMID of the panel's own host (and anything else precious) before first start, or an
  admin can saw off the branch the panel sits on.
- Destroy/reinstall require typing the exact VM name (checked server-side).
- Every NIC write path hardcodes `firewall=0`.
- All mutating endpoints require the session cookie + `X-Hlidskjalf-CSRF` header check.
- Rate-limit login (5/min), lock nothing else — single user.

## 5. Frontend spec

Pages (React Router):

1. **Fleet** `/` — table of all VMs/LXCs: status dot, name, VMID, VLAN, IP, CPU%, RAM,
   uptime, **traffic this month** (in+out, from `/api/bandwidth/summary`, sortable —
   instant top-talkers view); row click → VM page; quick start/stop icons.
2. **VM Overview** `/vm/:vmid` — the NorwayVPS layout: header with name + IP chip +
   action buttons (start=cyan, shutdown, reboot, stop=red outline, console); cards:
   Disk Usage (progress bar), Bandwidth (in/out split bar from netin/netout counters),
   CPU sparkline (last hour), Network sparkline; tabs: Overview / Graphs / Console /
   Settings / Tasks & Logs / Rescue.
3. **Graphs tab** — two sub-tabs like the reference panel:
   - **System Statistics**: timeframe pills (Hour/Day/Week/Month) + the five Recharts
     panels: CPU %, RAM (used vs max area), Disk usage, Disk I/O (read/write),
     Net rate (in/out). Radial "Utilization" gauges next to CPU/RAM/Disk.
   - **Bandwidth Statistics**: date-range picker (default: current month), summary
     card (Limit / Utilized / Utilization % when a quota is configured, otherwise
     just Utilized in/out/total), stacked daily area chart (in = cyan, out = pink,
     total = muted outline), and a Jan–Dec **Monthly Chart** stacked bar (in/out)
     fed by `/bandwidth/monthly`. Human units (auto KB→MB→GB→TB).
4. **Provision** `/new` — form (name, template select, cores, RAM, disk GB, VLAN
   select 20/30/50, static IP + gateway with per-VLAN gateway defaults, SSH key
   textarea prefilled from config, "start after create"), then a live task log.
5. **Console tab** — embedded noVNC canvas, connect/disconnect, Ctrl-Alt-Del button.
6. **Rescue tab** — explains what it does, Enter/Exit buttons, banner state.
7. **Node** `/node` — the node's own CPU/RAM/IO graphs + storage usage.

Poll `/api/vms` every 5 s on Fleet, `status/current` every 3 s on VM page.
Task-spawning actions poll the UPID until `stopped` and toast `OK`/`ERROR` with the
task exit status.

### Design language — superseded; see `docs/design/`

~~Tokyo Night, all-mono~~ is long gone. The current language is the v0.5.0
**cyberpunk pass**: palette pink `#ff4fa3` / cyan `#2de2e6` / deep night
`#15161f` (tokens in `frontend/tailwind.config.js`, emitted as `--c-*` CSS
vars — the single source of truth), Archivo for the human interface and
JetBrains Mono (`.metric`) for machine data. Glow is budgeted to live things
only; shape comes from chamfered shards and corner brackets; animation from a
fixed vocabulary. The spec, including the not-cringe guardrails:
`docs/design/v0.5.0-cyberpunk.md`.

## 6. Repo layout & flake (jivsan/hlidskjalf)

```
hlidskjalf/
  flake.nix            # outputs: packages.hlidskjalf, nixosModules.hlidskjalf, devShells
  backend/             # pyproject.toml (uv/hatchling), hlidskjalf/ package
  frontend/            # vite app; `npm run build` → dist/
  nix/
    package.nix        # buildNpmPackage for frontend → python312Packages.buildPythonApplication
                       # wraps uvicorn entrypoint, HLIDSKJALF_STATIC_DIR=frontend dist
    module.nix         # NixOS module (below)
  docs/bootstrap.md    # §3 verbatim
  plan.md              # this file
```

Pin `npmDepsHash` / use `package-lock.json`; standard nixpkgs idioms. Provide a
`devShell` with nodejs_22, python312, uv for local dev on mjolnir
(`uvicorn hlidskjalf.main:app --reload` + `vite dev` with proxy).

## 7. NixOS module (`nixosModules.hlidskjalf`)

Options (mirror the huginn.nix style):

```nix
services.hlidskjalf = {
  enable = lib.mkEnableOption "hlidskjalf Proxmox panel";
  port = lib.mkOption { type = lib.types.port; default = 8787; };
  environmentFile = lib.mkOption { type = lib.types.path; };   # secrets, see below
  settings = {
    pveHost = lib.mkOption { default = "<pve-host>"; };
    pveNode = lib.mkOption { default = "pve"; };
    pveTokenId = lib.mkOption { default = "hlidskjalf@pve!panel"; };
    pveFingerprint = lib.mkOption { type = lib.types.str; };    # sha256 pin
    rescueIso = lib.mkOption { type = lib.types.str; };         # "local:iso/systemrescue-...iso"
    protectedVmids = lib.mkOption { type = with lib.types; listOf int; default = [ ]; };
    bandwidthQuotas = lib.mkOption {
      type = with lib.types; attrsOf int;   # vmid (as string) -> GB per month, display-only
      default = { };
      example = { "115" = 500; };
    };
    defaultSshKeys = lib.mkOption { type = lib.types.lines; default = ""; };
    vlanGateways = lib.mkOption { default = { "20" = "192.168.20.1"; "30" = ""; "50" = "192.168.50.1"; }; };
  };
};
```

Implementation: `systemd.services.hlidskjalf` with `DynamicUser=true`,
`EnvironmentFile=cfg.environmentFile`, non-secret settings passed as env vars from
the options, `Restart=on-failure`, hardening (`ProtectSystem=strict`,
`PrivateTmp`, `NoNewPrivileges`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`).
`StateDirectory=hlidskjalf` for the tiny sqlite (rescue boot-order stash, session secret).

`environmentFile` (root-owned 0600, e.g. `/etc/hlidskjalf/env` — deploy however secrets
are handled today; agenix/sops-nix later):

```
HLIDSKJALF_PVE_TOKEN_SECRET=xxxxxxxx-....
HLIDSKJALF_ADMIN_USER=admin
HLIDSKJALF_ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
HLIDSKJALF_SESSION_SECRET=<openssl rand -hex 32>
```

(Generate hash: `nix run nixpkgs#python312 -- -c "..."` or `echo -n 'pw' | argon2 ...` —
document exact command in bootstrap.md.)

### Reverse proxy + exposure

Any reverse proxy that passes WebSockets works (no special config — the console
is a plain WS upgrade). LAN-only by default; tenants reach the panel through a
tunnel (see `docs/public-access.md` and `docs/newt-pangolin-tunnel.md`) with
`admin_networks` pinning admin functions to the LAN.

## 8. Phase 2 (later): Prometheus datasource

panel-host already runs Prometheus. Add `prometheus-pve-exporter` (nixpkgs:
`services.prometheus.exporters.pve`) pointed at the same `hlidskjalf@pve!panel` token
(PVEAuditor suffices for it), scrape it, then implement
`datasources/prometheus.py` against panel-host's Prometheus HTTP API for long-range
graphs (rrddata month granularity is coarse). The `MetricsSource` protocol from day
one makes this a drop-in: config flag `HLIDSKJALF_METRICS_SOURCE=rrd|prometheus`.
Grafana dashboards remain the deep-dive tool; the panel is the control surface.

## 9. Milestones — M0–M5 shipped; this is the roadmap now

- **M0–M5 — done.** Skeleton, read-only surface, power + console, provision +
  destroy, rescue + reinstall, and the first design pass all shipped and were
  validated read-only against real hardware (PVE 9.2.3, 2026-07-13). See
  `CHANGELOG.md` and `handoff.md`.
- **M6 — cyberpunk pass (v0.5.0, in progress)**: single source of truth for
  color (done), ambient scene, shard/glow language, signature surfaces, charts
  polish. Spec: `docs/design/v0.5.0-cyberpunk.md`.
- **Phase 3 — write-path validation (next)**: provision / reinstall / rescue /
  destroy against a real node, **scratch VMIDs ≥ 900 only**, with
  `HLIDSKJALF_PROTECTED_VMIDS` set before the panel ever starts. The mock suite
  being green means self-consistent, not correct — see CLAUDE.md for the
  assumptions most likely to be wrong (`scsi0` hardcoding, LXC destroy params,
  MAC/IP preservation, accumulator edge cases, cluster-vs-single-node).
- [ ] Secrets mechanism on panel-host today (plain root-owned env file vs sops/agenix).
- [ ] VLAN 30 gateway (storage VLAN may be gateway-less — provisioning form should
      allow empty gateway).

## 11. Planned — per-VM tenant reachability (SSH / VNC / TCP / UDP)

_Added 2026-07-15. The public tenant door now runs on **Pangolin + Newt** (a self-hosted
tunnel replacing Cloudflare). Newt carries **raw TCP and UDP**, not just HTTP — which is
exactly what SSH and a direct VNC/RDP path need and what Cloudflare's proxy could not do._

**Problem.** Today a tenant only gets the **panel login** exposed, and each resource is
enabled **by hand** in Pangolin. A friend with a VM has the browser console but no direct
way in. Adding SSH means manually creating a Pangolin resource per VM every time — it does
not scale and it is easy to get wrong.

**Feature: the panel provisions the tunnel resource when it provisions the VM.** Give each
tenant VM a reachable name (`<vps-name>.im-goat.com`) and, per protocol the tenant needs:

- **SSH** — a **TCP** resource → the VM's `:22`. Friend runs plain `ssh <vps>.im-goat.com`.
- **VNC/RDP** — a **TCP** resource → the VM's console/RDP port (direct client, not the
  browser console).
- **UDP** — for anything that needs it (e.g. WireGuard, game servers) — a **UDP** resource.

**How.** Drive it through Pangolin's declarative model so nothing is hand-clicked:

1. A per-VM `public_hostname` + a small set of requested ports (ssh on/off, extra
   TCP/UDP), stored with the VM.
2. On provision/enable, the panel emits the Newt **blueprint** entry (or calls the
   Pangolin API) for that resource; on destroy, it removes it. Blueprint targets point at
   the tenant VM's IP:port, method `tcp`/`udp` for raw, `http` for web.
3. Show the tenant the exact connection command on their VM page (`ssh …`, VNC address).

**Gates (do not skip — this exposes tenant machines):**
- Depends on the **tenant-VLAN** decision (handoff §"before the first tenant VM"): a friend
  who can SSH into their VM has a shell inside whatever network that VM sits on. Tenant VMs
  must not sit in an admin network.
- The Pangolin/Cloudflare API token is a secret — store it encrypted like the PVE token,
  scope it minimally, and never let a tenant reach the routes that use it.
- Per-VM authorisation still applies: a tenant may open/close only **their own** VM's
  resource, never anyone else's.

Supersedes the "reachable tenant VMs" note in handoff.md, which was written when the tunnel
was Cloudflare (HTTP-only) and therefore assumed Tailscale-on-every-VM for SSH.
