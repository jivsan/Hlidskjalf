# Hlidskjalf — working brief for Claude Code

A self-hosted, multi-user **Proxmox VE control panel**. FastAPI backend serving a
React SPA; one service, one port. Regular users are scoped to exactly one VM (the
VPS model); admins see the whole fleet.

`plan.md` is the design source of truth. `handoff.md` is "what's done / what's next"
and is kept current every session. `CHANGELOG.md` is Keep-a-Changelog format.

---

# ⚠️ READ THIS FIRST IF YOU ARE ON REAL HARDWARE

**v0.4.0-alpha has been tested against a real Proxmox VE 9.2.3 host** (2026-07-13,
read-only validation + the panel itself, live, from a Debian dev VM on the LAN).
The first-run wizard, fleet, node, graphs and **both consoles** work on real
hardware. See `handoff.md` for what that run found — and it found plenty.

**The write paths are still unproven**: nothing has been provisioned, reinstalled,
rescued or destroyed through the panel on real hardware. That is Phase 3.

All 219 backend tests pass — against `dev/mock_pve.py`, a mock **we wrote
ourselves**. It is our own assumptions reflected back at us. Green tests here mean
"self-consistent", not "works". **The mock has now been caught lying three times**:
8-field UPIDs (real PVE emits 9), fabricated QEMU disk usage (real PVE reports 0),
and a single echo websocket that made a container's console look identical to a
VM's — hiding the fact that neither console worked. Assume there are more.

## Hard safety rules — do not negotiate with these

1. **Never act on a VM you did not create.** Provision a scratch VM with **VMID ≥ 900**
   and do all destructive testing on that. Do not power-cycle, reinstall, rescue, or
   destroy anything else. Ever.
2. **Protect the panel's own host before you start.** `HLIDSKJALF_PROTECTED_VMIDS`
   now defaults to **empty** — meaning *nothing is protected* and an admin can destroy
   the VM the panel runs on. Set it to the VMID of the panel's host (and anything else
   you care about) **before** the first start:
   ```bash
   HLIDSKJALF_PROTECTED_VMIDS=<panel-host-vmid>,<anything-else-precious>
   ```
3. **Use a scoped, non-root API token.** Never `root@pam`, never a password, and
   **never `PVEAdmin`** (it carries Sys.Console/Sys.Syslog/User.Modify). Four narrow
   roles, each on the path that needs it:
   ```bash
   pveum user add hlidskjalf@pve
   pveum acl modify /vms       --users hlidskjalf@pve --roles PVEVMAdmin      # guests
   pveum acl modify /storage   --users hlidskjalf@pve --roles PVEDatastoreUser # clone disks
   pveum acl modify /          --users hlidskjalf@pve --roles PVEAuditor      # GET /nodes, tasks
   pveum acl modify /sdn/zones --users hlidskjalf@pve --roles PVESDNUser      # NIC -> bridge/VLAN
   pveum user token add hlidskjalf@pve panel --privsep 0     # prints the secret ONCE
   ```
   `--privsep 0` is mandatory (else the token gets its own empty ACL and can do
   nothing). **`PVESDNUser` is not optional on PVE 9**: without `SDN.Use`, every clone
   dies with `Permission check failed (/sdn/zones/.../vmbr1/20, SDN.Use)`. The panel's
   setup wizard prints these commands, generated from the token id you type.
4. **Read-only first.** Run `scripts/validate-proxmox.py` (read-only by default) before
   the panel touches anything. See `docs/real-hardware-validation.md`, and
   `docs/dev-against-real-proxmox.md` for the dev-VM setup.
5. **Never commit secrets.** `dev/dev.env`, tokens, fingerprints, and the state dir are
   gitignored — keep it that way. Never paste a token secret into a commit, a PR body,
   or a chat message.

## Settled against real hardware (do not re-litigate)

Confirmed on PVE 9.2.3, 2026-07-13 — these were the scary unknowns, and they held:

- **UPID format.** Real PVE emits 9 fields; `_vmid_from_upid` reads `parts[6]` and is
  **correct** — task-status authorisation is sound. (The *mock* was the liar.)
- **A scoped non-root token CAN call `GET /nodes`** → the setup wizard works.
- **rrddata / node-status shapes** match what `routes/metrics.py` normalises.
- **QEMU guest agent absent** → degrades correctly (500 from PVE, caught, falls back).
- **Cert pinning works.** `pve.py` refuses https without a fingerprint, by design:
  ```bash
  openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
  ```
- **Consoles differ by guest kind, and this is not negotiable:** QEMU → `vncproxy`
  (noVNC/RFB). LXC → `termproxy` (xterm.js). A container's `vncproxy` completes the
  RFB handshake and then **hangs forever at ClientInit** — that is Proxmox, not us.
  Also: negotiate the websocket subprotocol, never assert it (noVNC offers none).

## The assumptions still most likely to be wrong

The write paths — nothing here has ever run against real hardware:

- **`scsi0` is hardcoded** for template disk reads and resize (`routes/provision.py`).
  A template on `virtio0`/`sata0` silently never resizes.
- **`destroy-unreferenced-disks`** is passed for LXC destroy too; real PVE may 400 on
  the unknown param. The mock ignores query params entirely.
- **Reinstall must preserve MAC/IP**, and **rescue must restore boot order**.
- **Bandwidth accumulator**: counters must not double-count across a panel restart or
  go negative when a guest reboots.
- **Single node vs cluster.** The panel assumes ONE node (`pve_node`); pve is single-
  node, so the cluster path is still untested.

## What to do with what you find

Fix it, with a test that would have caught it, and open a PR. Update `handoff.md`.
If an assumption in the code is wrong, **also fix `dev/mock_pve.py`** so the mock stops
lying — otherwise the suite goes green again and we learn nothing.

---

# Running it

**Use the launcher — don't retype the commands.**

```bash
./scripts/dev.sh --mock      # no Proxmox: starts dev/mock_pve.py too. Panel on :8787.
./scripts/dev.sh             # real Proxmox, config from dev/dev.env
./scripts/dev.sh --reload    # + backend restarts on save
./scripts/dev.sh --vite      # + Vite on :5173 (open THAT url) with hot reload
```

It creates nothing it cannot recreate: first run builds the SPA, `--mock` needs no
secrets, and it warns loudly when `HLIDSKJALF_PROTECTED_VMIDS` is empty. It is a
**dev** launcher — production runs under systemd/Docker/Nix (see `README.md`).

Frontend changes only show up after `npm run build` (the backend serves `dist/`), or
use `--vite`.

Gotchas that have bitten every session:
- `HLIDSKJALF_PVE_NODE` must match the node name Proxmox actually reports, or every
  node-scoped endpoint 404s. The mock now uses Proxmox's own default (`pve`), so the
  mock stack needs nothing set — a **real** host with a different node name does.
- The switch eAPI is TLS-verified, so `mock_switch` needs its cert **and** the backend
  needs the pin (`HLIDSKJALF_SWITCH_FINGERPRINT`). See `dev/dev.env.example`.
- `HLIDSKJALF_COOKIE_SECURE=false` for plain-http dev, or the session cookie is never
  sent back.

## First run

The panel ships **unconfigured** and serves a setup wizard (Proxmox connection → admin
→ optional first user). See `docs/setup.md`. Deployments that set
`HLIDSKJALF_ADMIN_PASSWORD_HASH` seed an admin at startup and never see the wizard.

## Tests

```bash
cd backend  && ../.venv/bin/python -m pytest -q     # must be green
cd frontend && npx tsc --noEmit && npm run build    # must be clean, no chunk warning
```

---

# Conventions

- **Git identity: `jivsan <chrsol3@gmail.com>`.** Commit with
  `git -c user.name=jivsan -c user.email=chrsol3@gmail.com commit`.
  **Never add `Co-Authored-By` trailers of any kind.**
- Work on a branch, open a PR, merge it. Verify tests locally first — branch protection
  is not available on this repo (needs GitHub Pro), so nothing else will catch a red merge.
- **Keep `handoff.md` and `CHANGELOG.md` current** at the end of every batch of work.
- Frontend follows a deliberate design system — read `docs/design/v0.3.5-design-system.md`
  before touching UI. Archivo carries the human interface; **JetBrains Mono is reserved
  for machine data** (`.metric`). Don't introduce new colours, fonts, or animations.

# Security model (don't regress these)

- Scoped, non-root PVE token; the PVE TLS cert is **pinned by SHA-256 fingerprint**.
- Sessions are signed cookies **bound to the password they were issued under** — a
  password change invalidates every older session.
- Changing your *own* password requires the current one. CSRF on every mutation.
- Per-VM authorisation on **every** route (`_ensure_vm_access`); regular users see
  exactly one VM. Task status is scoped to the guest the UPID belongs to.
- Stored secrets (PVE token, session key) are **encrypted at rest** — see
  `secretbox.py`. The token is never written in plaintext and never returned by an API.
- Setup endpoints are unauthenticated **only** while no user exists; they close forever
  after that. Do not add a way to re-open them.
- **The panel may be exposed publicly — tenants only.** `admin_networks` (empty =
  anywhere) pins admin to a network; a tunnel puts the tenant panel on the internet.
  Enforced at login, at session use, and on every admin route — because a session cookie
  travels with the browser, so "admin from the LAN" has to mean the *request*, not the
  login. `trusted_proxies` decides whose `X-Forwarded-For` may be believed; without it
  the audit log records the proxy for everyone and the per-IP limiter is one shared
  bucket. See `docs/public-access.md`.

# Genericity — this ships to other people

**Nothing site-specific belongs in code — or in a tracked file at all.** The rule: a
`git clone` of this repo is a **fresh install**. Someone who clones it starts the panel,
meets the setup wizard, and configures *their* Proxmox. They must never find ours.

- IPs, VMIDs, storage names, bridges, node names, cert fingerprints and tokens live in
  `dev/dev.env` and `dev/site-notes.md` — **both gitignored**. Never in code defaults,
  tests, the mock, or a doc.
- The mock and the test fixtures are deliberately generic: node `pve`, guests
  `panel-host` / `vps-alpha` / `vps-beta` / `app-01` / `ct-runner`, example addresses in
  `192.168.<vlan>.<host>`. Keep them that way.
- `backend/tests/test_fresh_clone.py` enforces it: no tracked file may contain a real
  cert fingerprint or a token-shaped UUID, and `Settings()` with **no** environment must
  come up unconfigured (no host, no VLANs, no protected VMIDs, admin user `admin`).
- Config that used to be env-only (VLANs, clone storage, bridge) is editable in
  **Settings**. The install bar is "install → paste the Proxmox API token → set
  credentials → done".

The one sanctioned exception, for now: the **switch faceplate**, hardcoded to a
48-port Arista DCS-7050TX-48.

# Known limitations

- **Single Proxmox node only** — a cluster shows just `pve_node`.
- The switch faceplate does not render from what the switch actually reports. (The
  Switch page is optional — leaving `switch_host` unset hides it.)
- Provisioning is QEMU-only (LXC list/power/console work; LXC create does not).
- The panel **detects** updates (Settings → Updates) but does not apply them.
