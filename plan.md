# Hlidskjalf — self-hosted VPS panel for the hella Proxmox host

> A NorwayVPS/"flux"-style control panel, self-hosted on **heimdall**, talking to the
> Proxmox API on **hella** (10.0.20.10) with a **non-root, scoped API token**.
> Norse naming: *Hliðskjálf*, Odin's high seat from which he watches over all the realms —
> this is the high seat overlooking hella. `hlidskjalf` is used consistently below.

---

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
- Fleet list page showing all VMs/LXCs on hella at a glance.

Explicit non-goals (v1): multi-tenancy, billing, multiple Proxmox nodes, LXC provisioning
(LXC start/stop/monitor is fine — they come free from the same endpoints).

## 2. Architecture

```
mjolnir (dev/deploy) ──git push──▶ github.com/jivsan/Hlidskjalf
                                        │ flake input
                                        ▼
heimdall (NixOS VM on hella, 10.0.20.17)
  ├─ hlidskjalf.service (systemd, DynamicUser)
  │    FastAPI backend ── serves built React SPA as static files
  │    │
  │    ├── REST → https://10.0.20.10:8006/api2/json  (PVE API, token auth)
  │    └── WS proxy → wss://10.0.20.10:8006 ... vncwebsocket  (console)
  └─ Traefik ──▶ https://hlidskjalf.oryxserver.org  (existing *.oryxserver.org wildcard)
```

- **Backend**: Python 3.12, FastAPI + uvicorn + httpx (async) + `websockets` for the VNC proxy.
  No proxmoxer — the API surface used is small, a thin typed client in `pve.py` is cleaner.
- **Frontend**: React 18 + TypeScript + Vite + Tailwind, Recharts for graphs,
  `@novnc/novnc` for the console. Built at Nix build time, served by the backend
  (single service, single port, no CORS).
- **Repo**: new repo `jivsan/hlidskjalf`. Flake exports `packages.x86_64-linux.hlidskjalf`
  and `nixosModules.hlidskjalf`. The dotfiles repo adds it as a flake input and enables
  the module in `hosts/heimdall/`.

## 3. Manual bootstrap (Christina does this once, on hella's shell / PVE UI)

Claude Code: put these in `docs/bootstrap.md` verbatim; do not try to automate them.

### 3.1 PVE user, role scoping, API token (no root)

```bash
# On hella (Proxmox shell)
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
pveum aclmod /storage/local     --tokens 'hlidskjalf@pve!panel' --roles PVESDNUser  # typo guard: use PVEDatastoreUser here
pveum aclmod /sdn/zones/localnetwork/vmbr0 --tokens 'hlidskjalf@pve!panel' --roles PVESDNUser
```

> ⚠️ `pveum user token add` prints the secret **once**. It goes into the env file on
> heimdall (§7), never into git.

Pin the API TLS cert (self-signed) by SHA-256 fingerprint:

```bash
openssl s_client -connect 10.0.20.10:8006 </dev/null 2>/dev/null \
  | openssl x509 -fingerprint -sha256 -noout
```

### 3.2 Cloud-init template (Debian 13, VMID 9000)

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

> ⚠️ **`firewall=0` is mandatory** on every NIC with a VLAN tag on hella — `firewall=1`
> silently breaks tag propagation through the firewall bridge (documented fleet-wide bug).
> The panel must enforce `firewall=0` on every NIC it ever creates or edits. Hard rule.

Optionally repeat for Ubuntu 24.04 (VMID 9001). Any VM with `template=1` is
auto-discovered by the panel — no config needed per template.

### 3.3 Rescue ISO

```bash
# Into the ISO storage ('local' by default)
cd /var/lib/vz/template/iso
wget https://fastly-cdn.system-rescue.org/releases/latest/systemrescue-*-amd64.iso
```

The ISO volid (e.g. `local:iso/systemrescue-12.01-amd64.iso`) goes into the panel config.

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
      prometheus.py# stub with TODO — Phase 2 (heimdall Prometheus / pve-exporter)
```

PVE auth header: `Authorization: PVEAPIToken=hlidskjalf@pve!panel=<secret>`.
TLS: verify against pinned SHA-256 fingerprint (custom httpx transport), not `verify=False`.

### Endpoints

| Method | Path | Maps to PVE | Notes |
|---|---|---|---|
| POST | `/api/login` | — | argon2 verify, sets HttpOnly SameSite=Strict cookie |
| GET | `/api/vms` | `GET /cluster/resources?type=vm` | list incl. LXC, status, cpu, mem, uptime, tags |
| GET | `/api/vms/{vmid}` | `GET /nodes/hella/qemu/{vmid}/status/current` + `.../config` + agent `network-get-interfaces` | detail: IPs (via guest agent when running), disk bar, netin/netout counters |
| POST | `/api/vms/{vmid}/status/{action}` | `.../status/{start\|shutdown\|reboot\|stop\|reset}` | returns UPID; poll `/nodes/hella/tasks/{upid}/status` |
| GET | `/api/vms/{vmid}/metrics?timeframe=hour\|day\|week\|month&cf=AVERAGE` | `.../rrddata` | via MetricsSource; shape into `{t, cpu, mem, maxmem, disk, diskread, diskwrite, netin, netout}[]` |
| GET | `/api/node/metrics` | `/nodes/hella/rrddata` | host graphs for a node page |
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
| WS | `/ws/console/{vmid}` | `wss://hella:8006/.../vncwebsocket?port=..&vncticket=..` | bidirectional byte pump; auth via session cookie |
| GET | `/api/tasks/recent` | `/nodes/hella/tasks?limit=50` | "Tasks and Logs" tab |

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
- Missed intervals (panel down, heimdall reboot) are simply unaccounted — acceptable
  for a homelab; note it in the README. Worst case error = traffic during downtime.
- Persist the last-seen counters to sqlite every cycle too, so a panel restart
  doesn't double-count or lose the baseline (reload them on startup; if the VM
  restarted while the panel was down, the reset rule still catches it).
- Retention: keep daily rows forever (a few KB/year per VM); no pruning needed.
- **Quotas (optional, display-only)**: `bandwidthQuotas` maps vmid → GB/month
  (e.g. Jarvis's VPS-facing VMs). UI shows Limit / Utilized / Utilization % card;
  ≥ 80% renders the bar amber, ≥ 100% pink. No enforcement — this is a homelab,
  nothing gets suspended.
- Nice-to-have (not required for acceptance): on first run, seed the current month
  approximately by integrating rrddata `netin`/`netout` rates over the `month`
  timeframe so the charts aren't empty on day one.

### Provisioning flow (POST /api/vms)

1. Reject if name collides or protected list touched.
2. `POST /nodes/hella/qemu/{template_vmid}/clone` `{newid: next_free_id ≥ 200, name, full: 1, storage}` → wait UPID.
3. `PUT config`: `cores`, `memory`, `net0: virtio,bridge=vmbr0,tag={vlan},firewall=0`,
   `ipconfig0: ip={ip_cidr},gw={gateway}`, `sshkeys` (url-encoded), `ciuser=christina`,
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
  server-side** for these. Default must include heimdall's own VMID (the panel would saw
  off its own branch), hermes-agent, HAOS, and PBS (151). `shutdown`/`reboot` allowed.
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
7. **Node** `/node` — hella's own CPU/RAM/IO graphs + storage usage.

Poll `/api/vms` every 5 s on Fleet, `status/current` every 3 s on VM page.
Task-spawning actions poll the UPID until `stopped` and toast `OK`/`ERROR` with the
task exit status.

### Design language — Tokyo Night, not a NorwayVPS clone

Do not copy NorwayVPS branding/logo/layout pixel-for-pixel; take the information
architecture, restyle it as a native Tokyo Night instrument panel.

Tokens:
- bg `#1a1b26`, surface/cards `#24283b`, borders `#2f3549`, text `#c0caf5`, muted `#565f89`
- accents: **pink `#ff4fa3`** (primary actions, active tab underline, gauge sweep),
  **cyan `#2de2e6`** (positive/running, chart line 1), red `#f7768e` (destructive),
  amber `#e0af68` (rescue banner, warnings)
- Font: `JetBrainsMono Nerd Font, JetBrains Mono, monospace` everywhere — this is a
  terminal-native panel; the all-mono typography *is* the signature. Tabular numbers
  for all metrics.
- Charts: cyan→pink gradient fills at low opacity (echoes the rack-LED gradient),
  1.5px lines, no legends when a single series, grid lines `#2f3549`.
- Signature element: the VM header status chip renders like a systemd unit line —
  `● vps-jarvis-prod — active (running) · 10.0.20.15 · up 4d 2h` — green/red dot,
  mono, copy-on-click IP.
- Radius 8px, no glassmorphism, no gradients on surfaces, restrained motion
  (150 ms ease on tab underline and gauge sweep only).

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
    pveHost = lib.mkOption { default = "10.0.20.10"; };
    pveNode = lib.mkOption { default = "hella"; };
    pveTokenId = lib.mkOption { default = "hlidskjalf@pve!panel"; };
    pveFingerprint = lib.mkOption { type = lib.types.str; };    # sha256 pin
    rescueIso = lib.mkOption { type = lib.types.str; };         # "local:iso/systemrescue-...iso"
    protectedVmids = lib.mkOption { type = with lib.types; listOf int; default = [ 151 ]; };
    bandwidthQuotas = lib.mkOption {
      type = with lib.types; attrsOf int;   # vmid (as string) -> GB per month, display-only
      default = { };
      example = { "115" = 500; };
    };
    defaultSshKeys = lib.mkOption { type = lib.types.lines; default = ""; };
    vlanGateways = lib.mkOption { default = { "20" = "10.0.20.1"; "30" = ""; "50" = "10.0.50.1"; }; };
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
HLIDSKJALF_ADMIN_USER=christina
HLIDSKJALF_ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
HLIDSKJALF_SESSION_SECRET=<openssl rand -hex 32>
```

(Generate hash: `nix run nixpkgs#python312 -- -c "..."` or `echo -n 'pw' | argon2 ...` —
document exact command in bootstrap.md.)

### Traefik wiring (heimdall already runs Traefik + wildcard cert)

Add to heimdall's Traefik dynamic config in the dotfiles repo:

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

WebSockets pass through Traefik untouched — no extra config needed for the console.
DNS: `hlidskjalf.oryxserver.org` → 10.0.20.17 (match however grafana.oryxserver.org is done).
LAN-only exposure is assumed; do not add a public ingress.

### Dotfiles integration (Claude Code, in nixos-dotfiles repo)

1. `flake.nix`: input `hlidskjalf.url = "github:jivsan/Hlidskjalf";`
2. New `hosts/heimdall/modules/services/hlidskjalf.nix` importing the module + options above.
3. Deploy per house style: edit on mjolnir → push → pull on heimdall →
   `nixos-rebuild switch --flake`. Never hand-edit on the VM.

## 8. Phase 2 (later): Prometheus datasource

heimdall already runs Prometheus. Add `prometheus-pve-exporter` (nixpkgs:
`services.prometheus.exporters.pve`) pointed at the same `hlidskjalf@pve!panel` token
(PVEAuditor suffices for it), scrape it, then implement
`datasources/prometheus.py` against heimdall's Prometheus HTTP API for long-range
graphs (rrddata month granularity is coarse). The `MetricsSource` protocol from day
one makes this a drop-in: config flag `HLIDSKJALF_METRICS_SOURCE=rrd|prometheus`.
Grafana dashboards remain the deep-dive tool; the panel is the control surface.

## 9. Milestones for Claude Code

- **M0 — Skeleton**: repo, flake, devShell, FastAPI hello + Vite app served, login flow,
  NixOS module builds in a `nix flake check` VM test.
- **M1 — Read-only**: PVE client with fingerprint pinning, Fleet page, VM overview
  (status, config, agent IPs), rrddata graphs with timeframe picker, node page,
  **bandwidth accumulator + sqlite + Bandwidth Statistics tab** (accumulator is
  read-only against PVE, so it belongs here — the sooner it starts, the sooner
  history exists). *Acceptance: panel on heimdall shows live data for all hella VMs;
  after 24 h uptime, daily bandwidth rows exist for every running VM and survive a
  `systemctl restart hlidskjalf` without double-counting; a mid-day VM reboot does not
  produce a negative or absurd delta; token has never been root; nothing mutates.*
- **M2 — Power + console**: start/shutdown/reboot/stop with UPID polling and toasts;
  noVNC console via WS proxy. *Acceptance: full power-cycle of a scratch VM and an
  interactive console session through hlidskjalf.oryxserver.org.*
- **M3 — Provision + destroy**: template discovery, create form, clone flow with
  cloud-init + VLAN + `firewall=0` enforcement, destroy with name confirmation,
  protected-VMID guard, tasks log tab. *Acceptance: create a scratch Debian VM on
  VLAN 20 with static IP entirely from the panel, SSH into it, destroy it.*
- **M4 — Rescue + reinstall**: ISO rescue enter/exit with banner; reinstall preserving
  VMID/MAC/IP. *Acceptance: scratch VM rescued into SystemRescue and back; reinstall
  returns it to a fresh template state with the same IP.*
- **M5 — Polish**: Tokyo Night pass per §5, empty/error states, mobile-usable fleet
  page, README with screenshots.

Test everything against **scratch VMIDs ≥ 900** only. Never run destructive actions
against heimdall, hermes-agent, HAOS, or PBS (151) during development.

## 10. Open items Christina should confirm before/while M1 lands

- [ ] Actual storage IDs on hella for VM disks + ISOs (§3.1 assumes `local-lvm` / `local`).
- [ ] heimdall's, hermes-agent's, and HAOS's VMIDs → `protectedVmids`.
- [ ] Preferred subdomain (`hlidskjalf.oryxserver.org` assumed).
- [ ] Secrets mechanism on heimdall today (plain root-owned env file vs sops/agenix).
- [ ] VLAN 30 gateway (storage VLAN may be gateway-less — provisioning form should
      allow empty gateway).
