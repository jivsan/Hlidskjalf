# Dev VM against a real Proxmox

A scratch Debian VM you can break, pointed at a Proxmox you **cannot**. This is the
setup for first contact with reality — fast iteration, real tracebacks, and enough
guard rails that a mistake at 1am doesn't cost you a VM.

> **The dev box is disposable. Hella is not.** The panel holds a token that can stop,
> reinstall and permanently destroy guests. Everything below assumes that.

---

## 0. Safety rails — set these up before anything runs

1. **Only ever act on a VM you created for this.** Provision a scratch guest with a
   **VMID ≥ 900** and do every destructive test on that one. Never power-cycle,
   reinstall, rescue or destroy anything else.
2. **`HLIDSKJALF_PROTECTED_VMIDS` is env-only and defaults to EMPTY.** The setup
   wizard cannot set it (the setup endpoint is unauthenticated, so it writes only
   from a strict allowlist). If you don't set it, *nothing is guarded* — including
   the VM the panel is running on. Put every VMID you care about in it:

   ```bash
   HLIDSKJALF_PROTECTED_VMIDS=<dev-vm>,<heimdall>,<anything-precious>
   ```

   The Fleet page shows an amber banner whenever nothing is protected. If you see
   that banner, stop and fix it.
3. Destroy and reinstall additionally require typing the guest's exact name, checked
   server-side. That is a second net, not the first one.

---

## 1. On the Proxmox host: token, fingerprint, node name

```bash
pveum user add hlidskjalf@pve
pveum acl modify /vms       --users hlidskjalf@pve --roles PVEVMAdmin       # guests
pveum acl modify /storage   --users hlidskjalf@pve --roles PVEDatastoreUser # clone disks
pveum acl modify /          --users hlidskjalf@pve --roles PVEAuditor       # GET /nodes, tasks
pveum acl modify /sdn/zones --users hlidskjalf@pve --roles PVESDNUser       # NIC -> bridge/VLAN (PVE 9)
pveum user token add hlidskjalf@pve panel --privsep 0    # prints the secret ONCE
```

Two traps here, both of which produce a panel that authenticates fine and then fails
everything:

- **`--privsep 0` matters.** With `--privsep 1` the token is additionally restricted
  by its own ACL, which by default grants nothing.
- **`PVEAuditor` alone is not enough.** It grants no `VM.Console`, `VM.PowerMgmt` or
  `VM.Allocate`/`VM.Clone` — so console, power and provisioning would all 403. The
  role list above is the minimum that actually works.
- **`PVESDNUser` is not optional on Proxmox 9.** Attaching a NIC to a bridge/VLAN
  needs `SDN.Use`, and `PVEAuditor` grants only `SDN.Audit` (read). Without it every
  clone dies with `Permission check failed (/sdn/zones/localnetwork/vmbr1/20, SDN.Use)`.

The panel **pins** the Proxmox certificate, so grab its fingerprint:

```bash
openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
```

And get the **node name** — this is the PVE node, not the hostname, and the panel's
default is Proxmox's neutral `pve`:

```bash
pvesh get /nodes
```

---

## 2. On the dev VM: install

```bash
sudo apt update && sudo apt install -y git python3-venv nodejs npm

git clone https://github.com/jivsan/Hlidskjalf && cd Hlidskjalf
python3 -m venv .venv
.venv/bin/pip install -e ./backend python-multipart

# Build the SPA once; the backend serves it as static files.
cd frontend && npm ci && npm run build && cd ..
```

---

## 3. Validate BEFORE the panel touches anything

This is the whole point of the exercise. The script is **read-only** — it mutates
nothing without `--allow-writes` *and* a scratch `--vmid >= 900`.

```bash
.venv/bin/python scripts/validate-proxmox.py \
    --host <proxmox-ip> \
    --token-id 'hlidskjalf@pve!panel' \
    --fingerprint AA:BB:CC:...:FF
# --node is auto-detected from /nodes if you omit it
# the secret is prompted for, or pass --token-secret-file
```

It checks each assumption the panel is built on and prints PASS/FAIL with the value
it actually observed and the file each failure breaks. Read
`docs/real-hardware-validation.md` for what the checks mean and the manual checklist
(open a console and *type in it*; power-cycle the scratch VM; confirm a protected
VMID genuinely refuses destroy).

**When the script disagrees with the panel, the script is usually right.** All 184
tests pass against `dev/mock_pve.py`, a mock we wrote ourselves — it is our own
assumptions reflected back at us. It has already been caught lying once (it emitted
8-field UPIDs where real Proxmox emits 9, which silently 403'd every tenant polling
their own task).

---

## 4. Configure

For dev, an env file beats the setup wizard: it's reproducible, and you can wipe the
state dir and restart without re-typing anything. Create `dev.env`:

```bash
# --- the real Proxmox ---
HLIDSKJALF_PVE_HOST=10.0.20.10          # your host
HLIDSKJALF_PVE_NODE=hella               # from `pvesh get /nodes`
HLIDSKJALF_PVE_TOKEN_ID=hlidskjalf@pve!panel
HLIDSKJALF_PVE_TOKEN_SECRET=xxxxxxxx-xxxx-...
HLIDSKJALF_PVE_FINGERPRINT=AA:BB:CC:...:FF   # required for https — the cert is pinned

# --- SAFETY: env-only, empty by default. Do not skip this. ---
HLIDSKJALF_PROTECTED_VMIDS=<dev-vm>,<heimdall>,<anything-precious>

# --- panel ---
HLIDSKJALF_ADMIN_USER=admin
# argon2 hash — setting this seeds the admin and SKIPS the setup wizard:
#   .venv/bin/python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('devpass'))"
HLIDSKJALF_ADMIN_PASSWORD_HASH='$argon2id$v=19$...'
HLIDSKJALF_SESSION_SECRET=any-long-random-string-for-dev
HLIDSKJALF_STATE_DIR=/var/lib/hlidskjalf
HLIDSKJALF_STATIC_DIR=/home/you/Hlidskjalf/frontend/dist

# Plain http on the LAN -> the Secure cookie would never be sent back.
# Set this to true the moment you put it behind TLS.
HLIDSKJALF_COOKIE_SECURE=false

HLIDSKJALF_LOG_LEVEL=DEBUG
HLIDSKJALF_DEBUG=true          # enables the admin /debug page
```

Single-quote the argon2 hash — it contains `$` and a shell will eat it otherwise.

Leave `HLIDSKJALF_ADMIN_PASSWORD_HASH` **out** if you'd rather exercise the first-run
setup wizard instead (it takes the Proxmox connection, admin and first user in the
browser). `PROTECTED_VMIDS` still has to be in the env either way.

---

## 5. Run it

```bash
set -a && source dev.env && set +a
cd backend && ../.venv/bin/uvicorn hlidskjalf.main:app \
    --host 0.0.0.0 --port 8787 --reload
```

Open `http://<dev-vm>:8787`.

`--reload` restarts on every save — that iteration loop is the entire reason to run
native rather than in Docker for this phase.

**Iterating on the frontend too?** Run the Vite dev server instead of rebuilding:

```bash
cd frontend && npm run dev      # :5173, proxies /api and /ws to :8787
```

(and leave `HLIDSKJALF_STATIC_DIR` empty so the backend doesn't also try to serve a
stale `dist/`).

---

## 6. When it breaks — and it will

Watch the backend's output. It logs every request with timing, and the admin
**Debug** page (`/debug`, needs `HLIDSKJALF_DEBUG=true`) shows recent errors with
full tracebacks, the redacted config, and the durable audit log at
`/api/debug/audit`.

**The rule when reality disagrees with the code:** fix it, add the test that would
have caught it, *and fix `dev/mock_pve.py` so the mock stops lying*. Otherwise the
suite goes green again and we learn nothing. This is written into `CLAUDE.md`, so a
Claude Code instance on this VM already knows it.

### What I expect to break first

1. **The noVNC console.** The mock's "VNC" is a byte echo with no RFB server behind
   it, so the byte-pump has never moved a real frame. The specific risk:
   `routes/console.py` dials `vncwebsocket` with only the `PVEAPIToken` header, and
   Proxmox may insist on a `PVEAuthCookie` for websocket upgrades. If it 401s, the
   console needs redesigning around `/access/ticket`.
2. **Token privileges** — see the two traps in §1.
3. **Disk usage bars read empty.** Real Proxmox does not know a VM's in-guest disk
   usage without the guest agent; it reports 0. The mock fabricates 45%. Cosmetic,
   but it is the mock inventing data out of nothing.
4. **`destroy-unreferenced-disks` on LXC destroy** — the panel passes it for both
   guest kinds; real PVE may reject the unknown param with a 400. The mock ignores
   query params entirely, so this could never surface in tests.
5. **`scsi0` is hardcoded** for template disk reads and resize. A template on
   `virtio0`/`sata0` silently never resizes.

---

## 7. Optional: leave it running under systemd

Once you're past the edit-restart loop:

```ini
# /etc/systemd/system/hlidskjalf.service
[Unit]
Description=Hlidskjalf
After=network-online.target

[Service]
EnvironmentFile=/etc/hlidskjalf/dev.env
WorkingDirectory=/home/you/Hlidskjalf/backend
ExecStart=/home/you/Hlidskjalf/.venv/bin/uvicorn hlidskjalf.main:app --host 0.0.0.0 --port 8787
Restart=on-failure

# The env file holds the Proxmox token — keep it root-only (chmod 600).
StateDirectory=hlidskjalf
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now hlidskjalf
journalctl -fu hlidskjalf
```

### Getting the secret out of the env file

Stored secrets are always encrypted at rest, but by default the key sits in
`<state_dir>/secret.key` next to the database — which protects a copied `.sqlite3`,
not an attacker who can already read the state dir. For the real thing, hand systemd
the key instead:

```ini
LoadCredential=secret_key:/etc/hlidskjalf/secret.key
Environment=HLIDSKJALF_SECRET_KEY_FILE=%d/secret_key
```

Now the key lives in a tmpfs only the service can read. Every secret the panel takes
has a `_FILE` twin (`HLIDSKJALF_PVE_TOKEN_SECRET_FILE`, …) for the same reason — a
secret manager hands you a *file*, and an env var is visible in `/proc`. See
`docs/setup.md`.
