# Hlidskjalf

> *Hliðskjálf* — Odin's high seat, from which he watches over all the realms.  
> **v0.5.1-alpha** · internet-facing (tenants) with admin on the tailnet, on NixOS behind Traefik, against a real Proxmox VE 9.2.3 host

A self-hosted, multi-user **Proxmox VE control panel**: fleet overview, live graphs,
per-VM bandwidth accounting with monthly charts and quotas, provisioning from cloud-init
templates, reinstall, SystemRescue boot, and a working console for both VMs and
containers — all through a **non-root, scoped PVE API token**, with the Proxmox TLS
certificate **pinned by SHA-256 fingerprint**.

Each regular user is scoped to exactly one VM (the VPS model); admins see the whole
fleet. FastAPI backend serving a React SPA — **one service, one port**.

The interface is a deliberate cyberpunk instrument panel — ambient grid horizon,
neon-lit live indicators, chamfered shard actions, exactly one typed line on the
login screen — built on a strict budget: the palette never changes, glow only ever
touches *live* things, and `prefers-reduced-motion` freezes every animation.
Spec: **[docs/design/v0.5.0-cyberpunk.md](docs/design/v0.5.0-cyberpunk.md)**.

---

## Getting started

A clone of this repo knows **nothing** about anyone's setup — no host, no VLANs, no
VMIDs, no credentials. You start it, it serves a **setup wizard**, and you configure
your own Proxmox. That is the whole install:

> **install → paste a Proxmox API token → set your credentials → done.**

Steps 1–2 run on the **Proxmox host** and produce the two things the wizard asks for: a
scoped API token and the host's certificate fingerprint. Steps 3–5 run **wherever you
want the panel** — any machine that can reach Proxmox on port 8006. It does not have to
run on the Proxmox host, and it is better if it doesn't.

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

### 2. Decide how TLS is verified

**If Proxmox serves its stock self-signed certificate** — no CA can vouch for it, so the
panel pins it. Read the fingerprint of the certificate *actually being served*:

```bash
openssl s_client -connect <your-proxmox>:8006 </dev/null 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
```

Reading `/etc/pve/local/pve-ssl.pem` looks equivalent and often isn't: when a custom
certificate is installed, **`pveproxy` serves `pveproxy-ssl.pem` instead**, and the panel
will refuse to connect with a fingerprint that belongs to a certificate nobody presents.

**If Proxmox serves a real certificate** (Let's Encrypt / ACME / your internal CA), choose
**system-CA verification** in the wizard instead of a pin. An ACME certificate is reissued
every ~60 days and its fingerprint changes with it — a pinned panel would lose Proxmox on
renewal, on a schedule nobody remembers. Verifying the chain and hostname survives renewal
and still refuses an impostor. This needs the **hostname** the certificate was issued for,
not an IP.

There is no third option: the panel never talks to an unverified Proxmox over https.

### 3. Check the token before the panel ever uses it

Run this from wherever you are about to run the panel. It is **read-only** — it mutates
nothing unless you explicitly pass `--allow-writes --vmid <≥900>`:

```bash
git clone https://github.com/jivsan/Hlidskjalf && cd Hlidskjalf
python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart

HLIDSKJALF_PVE_TOKEN_SECRET='<the-secret-from-step-1>' \
.venv/bin/python scripts/validate-proxmox.py \
    --host <your-proxmox> --node <your-node-name> \
    --token-id 'hlidskjalf@pve!panel' --fingerprint <the-fingerprint-from-step-2>
```

It checks every assumption the panel is built on — token privileges, cert pinning,
`GET /nodes` with a scoped token, `/cluster/resources` shape, UPID parsing, rrddata,
guest agent, console websocket — and prints PASS/FAIL with the observed value and the
file each failure would break. **If it reports a missing privilege here, fix the token
now**; every one of them turns into a confusing failure inside the panel later.

`--node` must be the name Proxmox itself uses for the machine (the one in the left-hand
tree of the Proxmox web UI, often just `pve`). If it is wrong, every node-scoped page
404s.

### 4. Run the panel

Pick one. All three land on the same wizard.

**Docker** — nothing to build:

```bash
docker compose up -d          # see docs/docker.md
```

**NixOS** — a flake with a module (`services.hlidskjalf.enable = true;` and nothing else
is a working install). See **[docs/nixos.md](docs/nixos.md)**.

**From the clone** (what the dev box does — the launcher builds the SPA on first run):

```bash
./scripts/dev.sh              # panel on http://localhost:8787
./scripts/dev.sh --mock       # no Proxmox at all: also starts dev/mock_pve.py
./scripts/dev.sh --reload     # + restart the backend on every save
./scripts/dev.sh --vite       # + Vite on :5173 with hot reload (open THAT url)
```

`scripts/dev.sh` is a **development** launcher — it reads `dev/dev.env` if present and
warns loudly when nothing is protected. For a real deployment, run the installed
`hlidskjalf` console script under systemd, or use the NixOS module (`nix/module.nix`).
`docs/dev-against-real-proxmox.md` §7 has a working unit file.

<details>
<summary>…or by hand, if you'd rather see every moving part</summary>

```bash
cd dev     && ../.venv/bin/uvicorn mock_pve:app --port 18006 &     # optional mock
cd backend && set -a && source ../dev/dev.env && set +a \
           && ../.venv/bin/uvicorn hlidskjalf.main:app --port 8787 --reload
cd frontend && npm ci && npm run dev        # :5173, proxies /api and /ws to :8787
```
</details>

**Before that first start, protect the guests that must never be destroyed** — most of
all the one the panel itself runs on. This defaults to **empty**, which means *nothing*
is protected:

```bash
HLIDSKJALF_PROTECTED_VMIDS=<panel-host-vmid>,<anything-else-precious>
```

### 5. Finish in the browser

Open <http://localhost:8787>. Because no user exists yet, the panel serves the **setup
wizard** and nothing else:

1. **Proxmox connection** — host, port (8006), node name, `https`, the token id, the
   token secret from step 1, and the fingerprint from step 2. The wizard makes a **live
   API call** and refuses to save anything that doesn't answer.
2. **Admin account** — your username and password. This closes the setup endpoints
   *forever*: they are unauthenticated only while no user exists.
3. **First tenant** (optional) — a regular user pinned to exactly one VM.

You land signed in. Then set **Settings → provisioning**: your VLAN → gateway pairs, the
storage your VM disks actually live on, and the bridge your guests are actually on
(often **not** `vmbr0`). Proxmox's defaults are not universal, and provisioning fails
confusingly if these are wrong — the panel reads the real options off your node, so
they're dropdowns, not guesses.

**Prefer to configure declaratively?** Set the environment variables instead
(`hlidskjalf.env.example`) and the wizard never appears — **env always wins** over
anything the wizard stored, so secrets can live in agenix/sops/systemd-creds. Every
secret also takes a `*_FILE` twin, because a secret manager hands you a file.

Full walkthrough, including what each field means: **[docs/setup.md](docs/setup.md)**.

---

## What's in it

| | |
|---|---|
| **Fleet** | every guest on the node, live status, quick power actions |
| **VM detail** | overview, graphs, console, rescue, tasks — scoped per user |
| **Console** | **noVNC** for VMs, **xterm.js** for containers (Proxmox serves no working VNC for LXC — its RFB handshake hangs at ClientInit, so containers go through `termproxy`) |
| **Provision** | clone a cloud-init template, set VMID/cores/RAM/disk/VLAN/IP/SSH keys |
| **Bandwidth** | daily/monthly per-VM accounting with quotas |
| **Rescue** | boot a SystemRescue ISO, then restore the original boot order |
| **Users** | admins manage tenants; each tenant sees exactly one VM |
| **Settings** | the Proxmox connection (host, node, token, TLS) · VLANs → gateways, disk storage, network bridge — from what the node actually reports |
| **Updates** | the panel notices when a new commit lands on GitHub, and can apply it (opt-in) |
| **Profile** | change your own password (invalidates every other session) |

---

## Try it without a Proxmox at all

```bash
./scripts/dev.sh --mock       # starts dev/mock_pve.py too; panel on :8787
```

A fake Proxmox with a fake fleet (`panel-host`, `vps-alpha`, `ct-runner`, a couple of
templates). Everything works — fleet, graphs, provisioning, the container terminal —
against a host that does not exist. It is also how the tests run.

Be aware of what that means: `dev/mock_pve.py` is **a mock we wrote ourselves**, so it
can only ever reflect our own assumptions back at us. It has been caught lying three
times. Before you point the panel at a Proxmox you care about, run the validator
(step 3).

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

**v0.5.1-alpha runs on real hardware**: deployed on NixOS behind a Traefik reverse
proxy, against a real Proxmox VE 9.2.3 host. The first-run
wizard, fleet, node, graphs, both consoles, and **provisioning** work there —
a guest has been created end-to-end through the panel on the real host.
Earlier runs found defects that months of green tests never did — see
[`CHANGELOG.md`](CHANGELOG.md) and [`handoff.md`](handoff.md).

**Still being proven**: reinstall, rescue, and destroy have not formally passed on
real hardware yet. `scripts/phase3-write-paths.py` drives exactly that sequence
against a live panel on a scratch VMID ≥ 900 and prints per-step PASS/FAIL —
see `handoff.md` for the current state.

All 325 backend tests pass — against `dev/mock_pve.py`, **a mock we wrote ourselves**.
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
- **Exposing it is a deliberate, supported mode — for tenants only.** Set
  `admin_networks` (e.g. your tailnet) and admin stops existing outside it: an admin
  cannot log in from the internet, an admin session that leaves the network stops
  working, and admin routes refuse. Tenants sign in from anywhere and reach their one VM.
  Without `admin_networks`, the panel assumes a LAN and admin works from anywhere — so
  **do not put it on the internet in that state**. See [docs/public-access.md](docs/public-access.md).

---

## Layout

```
backend/    Python package `hlidskjalf` (FastAPI, PVE client, bandwidth accumulator)
frontend/   Vite + React + TS + Tailwind SPA (served by the backend as static files)
nix/        package.nix (frontend+backend build) and module.nix (services.hlidskjalf)
dev/        mock_pve.py — a fake PVE API, so everything runs without a real Proxmox
docs/       setup.md, docker.md, real-hardware-validation.md, dev-against-real-proxmox.md
scripts/    dev.sh (launcher) · validate-proxmox.py (check assumptions against a REAL host)
            · phase3-write-paths.py (prove provision/rescue/reinstall/destroy on a scratch VMID)
```

`plan.md` is the design source of truth. `handoff.md` is what's done and what's next.

**Nothing site-specific lives in the repo** — no host, no cert pin, no VMIDs, no token.
Your own deployment's facts go in `dev/dev.env` and `dev/site-notes.md`, both gitignored.
`backend/tests/test_fresh_clone.py` fails the build if a real certificate fingerprint or
a token-shaped secret ever reaches a tracked file, and if the panel's defaults ever stop
being "unconfigured". The mock's fleet is deliberately fictional.

## Screenshots

**[docs/screenshots/](docs/screenshots/)** — latest gallery is
[v0.5.0-alpha](docs/screenshots/v0.5.0-alpha/) (the cyberpunk pass: every page,
mobile, and a prefers-reduced-motion pass); [v0.3.6-alpha](docs/screenshots/v0.3.6-alpha/README.md)
includes the setup wizard. All captured against the development mock.

## Bandwidth accounting — known limits

Proxmox keeps no per-VM traffic history, so the panel samples the cumulative
`netin`/`netout` counters every 60 s and books deltas into sqlite (UTC days). Counter
resets on guest restart are handled; traffic while the panel itself is down is simply
unaccounted. The numbers are for capacity awareness, **not billing**.

## Known limitations

- **Single Proxmox node** — a cluster shows only the configured node.
- Provisioning is **QEMU-only** (containers list, power and console fine; LXC *create*
  is not implemented).
- The switch faceplate is hardcoded to a 48-port Arista DCS-7050TX-48 and does not
  render from what the switch reports. The Switch page is optional — leave
  `switch_host` unset and it disappears.
