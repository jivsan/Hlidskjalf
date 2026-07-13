# Hlidskjalf — working brief for Claude Code

A self-hosted, multi-user **Proxmox VE control panel**. FastAPI backend serving a
React SPA; one service, one port. Regular users are scoped to exactly one VM (the
VPS model); admins see the whole fleet.

`plan.md` is the design source of truth. `handoff.md` is "what's done / what's next"
and is kept current every session. `CHANGELOG.md` is Keep-a-Changelog format.

---

# ⚠️ READ THIS FIRST IF YOU ARE ON REAL HARDWARE

**This panel has never been run against a real Proxmox host.**

All 163 backend tests pass — against `dev/mock_pve.py`, a mock **we wrote
ourselves**. It is our own assumptions reflected back at us. Green tests here mean
"self-consistent", not "works". If you are running on Christian's LAN with access to
the real Proxmox host, **you are the first contact with reality** and that is the
whole point of you being there.

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
3. **Use a scoped, non-root API token.** Never `root@pam`, never a password.
   ```bash
   pveum user add hlidskjalf@pve
   pveum acl modify / --users hlidskjalf@pve --roles PVEVMAdmin,PVEDatastoreUser,PVEAuditor
   pveum user token add hlidskjalf@pve panel --privsep 0     # prints the secret ONCE
   ```
4. **Read-only first.** Run `scripts/validate-proxmox.py` (read-only by default) before
   the panel touches anything. See `docs/real-hardware-validation.md`, and
   `docs/dev-against-real-proxmox.md` for the dev-VM setup.
5. **Never commit secrets.** `dev/dev.env`, tokens, fingerprints, and the state dir are
   gitignored — keep it that way. Never paste a token secret into a commit, a PR body,
   or a chat message.

## The assumptions most likely to be wrong

Prioritise these — they are where the mock is most likely lying to us:

- **UPID format — security-critical.** `routes/vms.py::_vmid_from_upid` parses
  `UPID:node:pid:pstart:starttime:dtype:id:user:` and uses field 6 as the vmid to
  authorise task-status reads. If real PVE's format differs, the panel either 403s
  legitimate users **or fails open**. Verify against real UPIDs.
- **The noVNC console byte-pump has never been exercised against a real VNC endpoint.**
  The mock has no VNC. Open a console on a scratch VM and actually type in it.
- **Can a scoped (non-root) token call `GET /nodes`?** The setup wizard depends on it.
  If `PVEAuditor` doesn't grant it, first-run is broken for everyone.
- **Single node vs cluster.** The panel assumes ONE node (`pve_node`). A cluster will
  surface only that node. Confirm and report.
- **QEMU guest agent absent** → the panel must degrade, not 500.
- **rrddata / node-status shapes** (`memory` nested? `cpuinfo.cpus`?) — `routes/metrics.py`
  normalises both, but only against the mock's idea of them.
- **Cert pinning.** `pve.py` pins the PVE cert by SHA-256 and **refuses https without a
  fingerprint**. Get it from the host:
  ```bash
  openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
  ```

## What to do with what you find

Fix it, with a test that would have caught it, and open a PR. Update `handoff.md`.
If an assumption in the code is wrong, **also fix `dev/mock_pve.py`** so the mock stops
lying — otherwise the suite goes green again and we learn nothing.

---

# Running it

## Local dev (no Proxmox needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart
cp dev/dev.env.example dev/dev.env        # then fill ADMIN_PASSWORD_HASH + SWITCH_FINGERPRINT
cd dev  && ../.venv/bin/uvicorn mock_pve:app --port 18006 &
cd dev  && ../.venv/bin/uvicorn mock_switch:app --port 18080 \
             --ssl-certfile mock_switch.crt --ssl-keyfile mock_switch.key &
cd backend  && set -a && source ../dev/dev.env && set +a \
             && ../.venv/bin/uvicorn hlidskjalf.main:app --port 8787
cd frontend && npm install && npm run dev     # :5173, proxies /api to :8787
```

Gotchas that have bitten every session:
- `HLIDSKJALF_PVE_NODE` **must** be set (`hella` for the mock). The default is Proxmox's
  neutral `pve`, and node-scoped endpoints 404 if it doesn't match.
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

# Known limitations

- **Single Proxmox node only** — a cluster shows just `pve_node`.
- The switch faceplate is hardcoded to a 48-port + 4-QSFP Arista DCS-7050TX-48; it does
  not render from what the switch actually reports. (The Switch page is optional —
  leaving `switch_host` unset hides it.)
- Provisioning is QEMU-only (LXC list/power works; LXC create does not).
