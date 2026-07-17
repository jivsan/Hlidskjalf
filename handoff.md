# handoff.md — Hlidskjalf build status

_Last updated: 2026-07-17 (**v0.5.1-alpha**; PRs #74–#79 + release PR, 332 tests)._

## 🔒 Security hardening on top of v0.5.1 (from the adversarial audit)

A 7-dimension security audit (auth, per-VM authz, exposure/netzone, secrets,
pangolin, injection, XSS) ran over the internet-facing deployment model — 22 raw
findings, 17 confirmed after adversarial verification, each fixed with a
regression test that was proven RED against the old code first:

- **Duplicate `X-Forwarded-For` lines could forge the admin zone (HIGH, #79):**
  `netzone.client_ip()` read only the FIRST XFF header line. HTTP allows duplicate
  lines for list-valued headers (RFC 7230 §3.2.2), so a client behind the trusted
  proxy could put a spoofed admin-zone address on line 1 and the honest chain on
  line 2 — and be believed. That is the admin boundary itself. Fixed: read ALL
  lines (`getlist`, wire order = append order = chain order), concatenate, same
  right-to-left walk. Unit + end-to-end spoof tests, a legit two-hop admin login,
  and degenerate-line cases; the `_Req` mock now uses Starlette's real `Headers`
  (a plain dict cannot hold duplicate lines, so the attack was untestable before).
- **Console ticket mint rate-limited + audit log actually pruned (#78).**
- Remaining confirmed findings (env-seeded admin session revocation, session
  secret in startup log, login oracle, unbounded failure map, /api/session zone
  check, pangolin lifecycle) land in their own PRs — see below.

## ✅ v0.5.1-alpha — ITERATION ROUND TWO + PHASE 3 DRIVER

- **Design polish, round two** (screenshot-verified): toast shards with
  kind-colored accent edges, the hazard-striped RESCUE banner, glowing gauge arcs
  (#74); fleet row hover tick, masthead ignite-on-load, setup wizard + users +
  settings corner brackets (#76).
- **`scripts/phase3-write-paths.py` (#75):** the Phase 3 driver — the full write-path
  sequence (provision → console ticket → rescue in/out → reinstall → destroy) against
  a real panel's API on a scratch VMID ≥ 900, with per-step PASS/FAIL and a `finally`
  cleanup. Provisioning itself is already proven on real hardware (an operator-driven
  provision succeeded, and the driver PASSed provision + cleanup-destroy against the
  production panel); rescue/reinstall/destroy are what the driver still has to
  formally PASS on the real host — blocked on `HLIDSKJALF_RESCUE_ISO` being unset
  in the deployment (set `settings.rescueIso` in the NixOS module and re-run).

## 🔧 Security fixes in flight (branch `fix/security-high-auth`)

Four confirmed audit findings, each with a regression test that fails without the
fix (verified by reverting the source and watching them go red):

1. **Deleted env-seeded admin's sessions survived deletion** — `current_epoch` /
   `get_current_user` fell back to the env admin hash unconditionally when the
   username had no DB row. Both are now gated on an empty users table (the same
   condition `_legacy_verify` already required at login).
2. **Session signing secret in the startup log** — the config-shadow warning
   printed env + stored values verbatim, including `session_secret`. Secret-listed
   values are redacted; the warning still names the keys.
3. **403-after-password-verify was a credential oracle** — an out-of-zone VALID
   admin login got a distinct 403 while wrong passwords got 401. The client now
   gets the generic 401 either way; the refusal is logged + audited server-side.
4. **`/api/session` skipped the admin-zone boundary** — real: it returned 200
   (role=admin + CSRF) to an out-of-zone admin cookie via `require_session_full`.
   Now zone-checked; `/api/logout` deliberately stays zone-free (revocation only
   ever reduces access).


## ✅ v0.5.0-alpha — FULL SEND, NOT CRINGE

The whole frontend was reskinned per `docs/design/v0.5.0-cyberpunk.md` (the spec was
written **before** the CSS and supersedes `v0.3.5-design-system.md`'s "no new
animations / make it quieter" while keeping its palette, type split and density).
Shipped as a series of small PRs, one per work item:

- **Foundation (#62):** `tailwind.config.js` is the single source of truth for color
  and emits every token as a `--c-*` CSS var; Recharts/xterm/noVNC read them via a new
  `cssVar()` helper (the stale hardcoded `CHART` palette is gone). `green` tokenized;
  Switch page deduplicated onto the shared bytes-based rate formatter.
- **The ride-alongs:** `/api/version`'s git subprocesses moved off the event loop
  (#63); `dev.sh` now actually serves the SPA it builds (#66 — it never exported
  `HLIDSKJALF_STATIC_DIR`); `nix/package.nix` version caught up (CI's
  `test_nix_package.py` caught the miss — the test doing its job).
- **Ambient scene (#65):** drifting aurora, perspective grid horizon (60s seamless
  loop), static scanlines (~5%, never animated), vignette. Fixed layers, transform/
  opacity only, plain HTML in `index.html`.
- **Shape + glow (#67):** chamfered shard buttons/badges (clip-path ⇒ borderless,
  drop-shadow follows the silhouette = neon hover + focus indicator), corner
  brackets, the glow budget (wordmark / live dots / VM chip / shard silhouettes),
  `dot-bloom` replacing `animate-ping` on live dots.
- **Signature surfaces (#71):** the login hero — CRT `power-on`, `neon-ignite`
  wordmark, and **exactly one typed line** (`> the high seat — watching every guest`;
  types once, any key completes, cursor retires, reduced-motion renders complete,
  names no host). The nav marker glides between items with a cyan bloom.
- **Charts (#69):** primary strokes draw in once on mount (never per-poll), faint
  bloom on the main line only.
- **Verification (#70):** `docs/screenshots/v0.5.0-alpha/` — every page, a tenant
  view, the setup wizard, a 390px mobile pass, and a `prefers-reduced-motion` pass
  proving every animation dies and the scene goes static. We read the PNGs and
  iterated (the grid was too faint at first — presence pass landed in the ambient PR).
- **plan.md** was brought back to truth along the way (status header, roadmap, stale
  bootstrap/Tokyo Night/site specifics removed; #64).

Also merged this batch: **Pangolin SSH-tunnel auto-provisioning** (#61) and the
tenant-tunnel docs move to Pangolin + Newt (#55).

**Next up: Phase 3 — real-hardware validation of the write paths** (provision /
reinstall / rescue / destroy on scratch VMIDs ≥ 900, `HLIDSKJALF_PROTECTED_VMIDS`
set first). The mock suite being green means self-consistent, not correct — the
assumptions most likely to be wrong are listed in CLAUDE.md.

## ✅ v0.4.4-alpha — EXPOSED TO THE INTERNET, TENANT-ONLY, HARDENED

The panel is now reachable from the internet for tenants, with admin pinned to the
tailnet — and the exposure was audited and hardened before and after going live.

- **`public` interlock (#56):** `HLIDSKJALF_PUBLIC` refuses to start unless
  `admin_networks` + `trusted_proxies` are both set, so an unsafe exposure (admin login
  open to the world, or blind to the caller) cannot be deployed by accident.
- **CF-Connecting-IP spoof fixed (#57, HIGH):** the header is ignored unless
  `HLIDSKJALF_CLOUDFLARE` is set. A non-Cloudflare proxy (Traefik, Newt/Pangolin)
  forwards it verbatim, so trusting it let a client name any source IP and land inside
  `admin_networks` — reaching admin login from the internet and evading the per-IP login
  limiter. Now off by default; only the `X-Forwarded-For` walk is trusted.
- **Defense-in-depth (#58):** `GET /api/switch/ports` is admin-only (was a tenant L2
  topology leak); `POST /api/setup` + `/api/setup/test` respect `admin_networks`;
  `POST /api/logout` requires CSRF.
- Front it with a tunnel/reverse proxy and set `public`, `admin_networks`,
  `trusted_proxies` (and `cloudflare` only behind Cloudflare). **Tenant VMs are not
  exposed** — a tenant reaches their one VM *through* the panel (console/SSH) after
  logging in. Full model + the `cloudflare` nuance in `docs/public-access.md`.

## ✅ v0.4.2-alpha — THE PANEL IS DEPLOYED

It runs on a real NixOS host, behind Traefik, against a real Proxmox. **Every bug in this
release was found by deploying it** — none by the test suite, which was green throughout:

| found by deploying | verdict |
|---|---|
| NixOS module emitted `HLIDSKJALF_PVE_NODE=pve` from its own default, and **env beats the wizard** — every node-scoped page died with `hostname lookup 'pve' failed` | **module** was wrong: options are `nullOr`/null now, and `test_nix_module.py` fails the build if that ever regresses |
| `nix/package.nix` was missing `cryptography` — Nix gives a Python app ONLY what the list names, so it would have built green and crashed on first request | **package** was wrong (#48); `pythonImportsCheck` + `test_nix_package.py` |
| The panel's own error text blamed the credentials for a **TLS pin mismatch** | **panel** was wrong: unreachable / rejected / refused are three messages now |
| Proxmox's cert was **Let's Encrypt**, not `pve-ssl.pem` — pveproxy serves `pveproxy-ssl.pem` when a custom cert exists, and the pin dies on every renewal | **panel** was wrong: `pve_tls = "system"` verifies chain + hostname instead |
| The Proxmox connection was settable **only in the wizard**, which closes forever | **panel** was wrong: Settings → Proxmox, admin + CSRF + live test, audited |
| Debug page permanently empty on Nix (buffers need `HLIDSKJALF_DEBUG`) and would not say why | **panel + module** were wrong |
| Update detection is **useless without a git checkout** — a Nix/Docker panel could only say "cannot compare commits" | **panel** was wrong: non-git installs compare **releases** now |

**There is deliberately no "reset to wizard".** The setup endpoints are unauthenticated by
construction; reopening them would be a takeover window on the LAN. A factory reset takes
shell access on the host (stop, delete the state dir, start) — and that is the point.
`test_pve_connection.py` asserts the setup endpoints stay shut.

_Previously updated: 2026-07-13 (**v0.4.0-alpha — tested against real hardware**; PRs #32–#41, 219 tests). The design source of truth is `plan.md`; this file is only "what is done / what's next"._

## ✅ v0.4.0-alpha — THE PANEL HAS RUN AGAINST A REAL PROXMOX HOST

**This is no longer a panel that has only ever met a mock.** It was run against a
real Proxmox VE 9.2.3 host (`pve`) on 2026-07-13, from a Debian dev VM on the
same LAN: read-only validation, then the panel itself, live, with a real scoped
token — first-run wizard, fleet, node, graphs, and both consoles.

**What reality changed, that no test had caught:**

| found on real hardware | verdict |
|---|---|
| Wrong TLS-pin negative test (`wrap_socket` doesn't use `sslobject_class`) | panel was right, **validator** was wrong (#32) |
| PVE 9 renamed the guest-agent privilege to `VM.GuestAgent.*` | **validator** map outdated (#33) |
| QEMU `disk` is always 0 on real PVE | **mock** fabricated 45% (#34) |
| **Containers have no working VNC** — LXC `vncproxy` hangs at ClientInit | **panel** was wrong: containers need termproxy (#39) |
| **Every VM console died on arrival** — noVNC offers no subprotocol, panel asserted `binary` | **panel** was wrong (RFC 6455 §4.1) (#40) |
| Provisioning could not work at all — `vlan_gateways` empty ⇒ every create rejected | **panel** was wrong: now editable in Settings (#38) |

Both consoles now work on real hardware: a **QEMU VM** opens a live noVNC
framebuffer, an **LXC container** opens a live xterm.js shell.

Green tests prove self-consistency, not correctness — `dev/mock_pve.py` is a mock
we wrote ourselves, and it has now been caught lying **three** times (8-field
UPIDs, fabricated QEMU disk usage, and one echo websocket that made a container's
console look identical to a VM's). Each was fixed in the mock too. **Assume there
are more.** Read `CLAUDE.md` first — the hard safety rules are not negotiable.
Setup for the dev box: `docs/dev-against-real-proxmox.md`.

### ✅ Phase 1 — DONE (2026-07-13, against a real host, PVE 9.2.3)

`scripts/validate-proxmox.py` ran read-only against the real host: **33 pass,
1 FAIL, 4 warn**; after triage (PRs #32/#33/#34) it is **35 pass, 0 FAIL**. The
scary assumptions HELD on real hardware: 9-field UPIDs parse correctly in
`_vmid_from_upid` (task authz is sound), **the console websocket handshakes with
the `PVEAPIToken` header alone and a real RFB server greeted back**, a scoped
token can `GET /nodes` (setup wizard lives), rrd/node/task shapes all match.
Triage results: the FAIL was the *validator's* negative TLS test probing the one
TLS API the panel never uses (`wrap_socket`); the pin now covers both paths
(#32, defense-in-depth). One WARN was PVE 9 renaming the guest-agent privilege
to `VM.GuestAgent.*` (#33, validator map fixed). One was the mock fabricating
QEMU disk usage (#34, mock now honest). The rest are environment facts (below).

**What real hardware taught us — the generic lessons** (this repo is public and ships
to other people, so it holds no one's host facts. **Your** node name, host, cert pin,
storage names, VMIDs and token live in `dev/site-notes.md`, which is gitignored, next
to `dev/dev.env`. `backend/tests/test_fresh_clone.py` fails the build if any of it
leaks back into a tracked file):

- **`local-lvm` is only Proxmox's usual default, not a guarantee.** A host whose VM
  disks live on some other storage rejects every provision until
  `HLIDSKJALF_CLONE_STORAGE` (or Settings → provisioning) names the real one.
- **Guests are often not on `vmbr0`.** Same story — set the bridge in Settings.
- **Stock cloud images ship without `qemu-guest-agent`**, so a fresh clone shows no
  in-guest IP or disk usage until it is installed. That is the guest's fault, not
  the panel's; it degrades correctly.
- **The token needs four roles, not three:** `PVEVMAdmin,PVEDatastoreUser,PVEAuditor,
  PVESDNUser` (privsep 0). Without **PVESDNUser** every clone dies with
  `Permission check failed (/sdn/zones/<zone>/<bridge>/<vlan>, SDN.Use)` — PVE 9 gates
  attaching a NIC to a bridge/VLAN behind `SDN.Use`, and PVEAuditor grants only
  `SDN.Audit` (read). The setup wizard now prints all four, generated from the token
  id you type.
- **Set `HLIDSKJALF_PROTECTED_VMIDS` before the first start.** It defaults to empty,
  which means *nothing* is protected — including the guest the panel runs on.
- The repo is **public**, with ruleset `protect-main`: PRs + green `backend`/`frontend`
  CI required, no force-push. Never paste a token secret into a commit, a PR, or a chat.

### ✅ Phase 2 — DONE (2026-07-13). The panel ran against the real host.

Started with `HLIDSKJALF_PROTECTED_VMIDS=151,152,153,154,155,201` (env-only,
empty by default — **set it before the first start or nothing is guarded**), no
Proxmox connection in the env, so the **first-run wizard** was exercised for real:
host + node + token + fingerprint → live-validated → admin → signed in. It worked.

Found and fixed while the panel was up (all merged):
- **Both consoles were broken, in two different ways** (#39, #40 — see the table
  above). They now work: QEMU → noVNC framebuffer; LXC → xterm.js terminal.
- **Provisioning was impossible** without hand-written env vars (#38) → the new
  admin **Settings** page (VLANs, clone storage, bridge, from live node data).
- The wizard asked for a "scheme" and an "optional" fingerprint with no hint how
  to get one (#36) — both now say what they mean, and https requires the pin.
- No way to change your own password (#37) → `/profile`.

### The remaining plan, in this order. Do not skip ahead to the fun part.

**Phase 3 — writes. NEXT SESSION. Scratch VM only, VMID ≥ 900.**
1. Set `HLIDSKJALF_CLONE_STORAGE=vm-store`, then in **Settings → provisioning**
   add the VLAN → gateway you use and the bridge your guests are actually on (it is
   often NOT vmbr0).
2. Provision a scratch guest *from the panel*, from the Debian template — **type
   `900` in the new VMID box**, so everything below stays inside the ≥ 900 fence by
   construction rather than by luck. Then, on that guest only:
3. Power-cycle it, and **watch the task poll actually complete** (the UPID path —
   validated read-only, never yet driven end-to-end through the panel's poller).
4. ~~Open the console and type in it~~ — **DONE, both kinds work** (#39, #40).
5. Rescue enter/exit (boot order must be restored). Reinstall (MAC/IP preserved).
6. Destroy it. Then confirm a **protected** VMID genuinely refuses destroy.
7. Create a regular user, assign it the scratch VM, and confirm it can see *only*
   that VM — and that it can poll its own power-action task.

Watch for the two write-path assumptions the mock still cannot test:
`destroy-unreferenced-disks` (the panel passes it for LXC too; real PVE may 400)
and **`scsi0` is hardcoded** for template disk reads and resize (a template on
`virtio0`/`sata0` silently never resizes).

**Phase 4 — the slow one.**
8. After ~24 h: daily bandwidth rows exist for every running guest, survive a restart
   without double-counting, and a mid-day reboot produces no negative delta.

### ⚠️ Before the first tenant VM exists: give tenants their own VLAN

The panel is now exposed to the internet for tenants (Pangolin + Newt tunnel — see the
2026-07-15 note up top; was Cloudflare), with admin pinned to `admin_networks`. If those networks include a VLAN that tenant VMs also sit on,
a tenant with a shell on their own VM is *inside an admin network* — they would still
need a password, but they can reach the admin login page and everything else on that
segment. Provision tenant guests onto a VLAN that is **not** an admin network and has no
route to one. Decide this before the first friend's VM is created; retrofitting means
renumbering a machine somebody else is using.

### Queued — reachable tenant VMs: `<vps-name>.im-goat.com` so friends can SSH in

**Asked for 2026-07-14.** A friend with a VM needs to get into it. The panel gives them a
console; SSH is what they will actually want. The naming should follow the panel's own
public domain: one hostname per VM.

**The trap: Cloudflare's proxy carries HTTP/HTTPS only.** An orange-clouded record does
not forward port 22 — raw TCP through Cloudflare is Spectrum, which is an Enterprise
product. So "point the DNS at Cloudflare" does not give anyone SSH. The real options,
honestly compared:

| approach | what the friend does | what it costs you |
|---|---|---|
| **Cloudflare Tunnel + Access (SSH)** | installs `cloudflared`, then `ssh -o ProxyCommand='cloudflared access ssh --hostname vps.im-goat.com'` | one tunnel ingress entry + one Access policy per VM. No inbound ports, origin IP stays hidden. Friends need a client and a Cloudflare login. |
| **Tailscale on the tenant VM** | installs Tailscale, joins (node sharing / auth key) | nothing exposed at all, no ports, no DNS. Arguably the best answer, and you already run Tailscale. The VM must be allowed to reach the tailnet. |
| **DNS-only record + port forward** | plain `ssh vps.im-goat.com` | **exposes your home IP**, one NAT rule per VM, and puts sshd on the internet. Simplest for the friend, worst for you. |

**Recommendation: Tailscale for shell access, the tunnel for the panel.** Keep the
Cloudflare tunnel doing what it is good at (the tenant panel over HTTPS) and let Tailscale
do what it is good at (getting a person into a machine). If tunnel-based SSH is chosen
anyway, prefer it over the port-forward — never publish the origin IP.

**Panel work this implies (none of it exists yet):**
- a per-VM `public_hostname` field, shown to the tenant on their VM page, with the exact
  connection command for whichever method is chosen;
- optional automation: create the DNS record / tunnel ingress entry when a VM is
  provisioned, and remove it when the VM is destroyed. That needs a **Cloudflare API
  token** — scope it to DNS:Edit on the one zone, store it encrypted like the PVE token,
  and never let a tenant reach the routes that use it;
- and it depends on the **tenant VLAN** decision above: a friend who can SSH into their VM
  is a friend with a shell inside whatever network that VM sits on.

### Also queued (asked for, not yet built)
- **Choose VM login credentials at provision time** — ✅ **done, unreleased** (branch
  `feat/provision-credentials`). Root cause of "username or password is wrong" on a fresh
  clone: the panel wrote `ciuser` + `sshkeys` but **never `cipassword`**, and a cloud
  image has no password of its own. The Provision form now takes a login user +
  password; the backend writes `cipassword` (Proxmox hashes it) and never logs or returns
  it. It also **refuses** a create with no password and no SSH key — the exact trap —
  with a test that would have caught it. **Two gaps, on purpose:** (1) reinstall does not
  yet take credentials, so a reinstalled VM still relies on `default_ssh_keys`; (2) an
  *already-created* unloginnable VM (e.g. the first newt attempt) must be **destroyed and
  re-created** with a password. **Untested on real hardware** — it is a write path
  (Phase 3); the mock stores `cipassword` verbatim, real PVE hashes it and redacts it on
  read, but the panel never reads it back so that difference is invisible to us.
- **Choose your own VMID** — ✅ **done, unreleased** (branch `feat/choose-vmid`). The
  Provision form has a VMID box prefilled with the next free id; empty still means
  "next free". `vmid` is optional on `POST /api/vms`, and the backend refuses a taken
  one (409) or a protected one (403, audited) — a clone writes to whatever `newid` it
  is handed, so that check is the whole point. `GET /api/provision/defaults` now
  returns `used_vmids` / `protected_vmids` so the form can say so before you submit.
  **Untested on real hardware** — it is a write path (see Phase 3).
- **Self-update** (`POST /api/update`) — ✅ landed in v0.4.1-alpha, opt-in behind
  `HLIDSKJALF_ALLOW_SELF_UPDATE`, verified live against GitHub. See the changelog.
- **NixOS deployment** — ✅ **done, unreleased**. `nix/package.nix` had never been built;
  it was missing `cryptography` (Nix gives a Python app only what that list names, so it
  would have built fine and crashed on first request), still had `lib.fakeHash`, version
  `0.1.0`, nixpkgs 24.11. Now builds, starts, serves the SPA and the wizard — verified on
  a real NixOS 26.05 host. The module is wizard-first (`enable = true` and nothing else),
  with `bindAddress` / `cookieSecure` / `openFirewall` / `updateCheckEnabled` and warnings
  for the two silent-failure traps (nothing protected; Secure cookie over plain http).
  See `docs/nixos.md`. **Self-update stays refused on Nix by design** — a Nix system
  updates from its flake: `nix flake update hlidskjalf && nixos-rebuild switch`.
- **The switch faceplate is still hardcoded** to a 48-port Arista DCS-7050TX-48.
  Christian's call: this one may stay site-specific for now. Everything else must
  be generic — no homelab baked into code.

### Reporting back
Commit as `jivsan <chrsol3@gmail.com>`, no `Co-Authored-By` trailers. Push branches
and open PRs. Update this file and `CHANGELOG.md`. If you cannot open a PR (no GitHub
token on that box), just push the branch — Christian will merge.

---

## ⚡ Current state — v0.4.0-alpha

**The release that was tested against real hardware.** `main` is green:
**219 backend tests**, `tsc` + `vite build` clean, no chunk warnings.

New in v0.4.0-alpha (all found or driven by the real-hardware run above):
- **Settings page** (admin): *provisioning* tab — VLAN tags → gateways, clone
  storage, bridge, with options read from what the node actually reports; and an
  *updates* tab (below). Env still wins; env-locked keys refuse edits.
- **Update detection** — `GET /api/version` compares the running commit with the
  tip of `main` on GitHub: push, merge, and the panel notices it is N commits
  behind, with the list. Fail-soft (no network → no offer, no error, no nag),
  never phones home with anything identifying, never claims "up to date" without
  actually comparing, and treats an *ahead* checkout as ahead — not behind. It
  prints the honest update command for the detected deployment (docker / nix /
  git) and **does not update itself**: an endpoint that runs new code on demand is
  a bigger hole than anything it protects.
- **Profile page** — click your username → change your own password.
- **Both consoles fixed** (#39, #40) — QEMU noVNC and LXC xterm.js, live on real hardware.

### The v0.3.6-alpha foundation (still true)

It was wired to one homelab; it now ships unconfigured and sets itself up in a
browser.

- **First-run setup wizard** (`routes/setup.py`, `docs/setup.md`). Start with no env
  file → the panel serves a wizard: Proxmox host/node/token (validated with a LIVE
  call before anything is persisted), admin account, optional first user → signed
  straight in. **The security invariant: setup is reachable IFF the users table is
  empty**; once an admin exists every `/api/setup/*` returns 409 forever (it is
  unauthenticated by necessity, so re-opening it would be a takeover backdoor).
  Config persists to a `config` table; **env always wins** (agenix/sops users are
  never overridden); a `SETUP_WRITABLE` allowlist bounds what it can write.
- **Security audit** — patched: sessions surviving a password change (HIGH), self
  password-change needing no current password (HIGH), `/api/tasks/{upid}/status`
  IDOR (MED), console WS key not bound to its minter (MED), missing security
  headers (MED), login username-enumeration timing (LOW). Details in CHANGELOG.
- **Genericity**: config defaults no longer bake in a site (`pve_host` required,
  `pve_node` → `pve`, `admin_user` → `admin`, protected_vmids/vlan_gateways/
  switch_host empty). Node name comes from `/api/session`; the UI renders it
  instead of hardcoding a host.
- **Prometheus datasource** (Phase 2, `HLIDSKJALF_METRICS_SOURCE=prometheus`,
  `docs/prometheus.md`). rrd stays default.
- **Bundle code-split**: first paint 633 kB → 182 kB (−71%).
- Fixed a fatal bootstrap bug: `PveClient` refuses https without a fingerprint, so
  an *unconfigured* install used to crash on startup and could never be configured.
- **Secrets at rest (v0.3.6, later PR)**: the Proxmox token is **never stored in
  plaintext**. `secretbox.py` encrypts stored secrets (Fernet); the key comes from
  `HLIDSKJALF_SECRET_KEY(_FILE)` (systemd-creds / Docker / k8s — survives a stolen
  disk) or, failing that, a generated `<state_dir>/secret.key` (0600, separate file
  from the DB — protects the realistic "someone copied the .sqlite3" accident, not
  local root; docs say so plainly). Every secret also accepts a `*_FILE` env twin,
  because secret managers hand you a file, not an env var. A DB that cannot be
  decrypted makes the panel refuse to start rather than run on garbage.

### 🎯 NEXT SESSION — "update from GitHub and it just works"

Requested feature, **not built yet**. Goal: an operator running Hlidskjalf can take
a new release without hand-pulling, hand-building, or reading a migration note.

**Design (proposed — argue with it before building):**

1. **Version + channel.** Ship the version in the image/package and expose
   `GET /api/version` → `{current, latest, update_available, notes_url}`. `latest`
   comes from the GitHub Releases API (`/repos/jivsan/Hlidskjalf/releases/latest`),
   cached ~1 h, and **must fail soft** — no network, no problem, the panel just
   doesn't offer an update. Never phone home with anything identifying.
2. **The update mechanism depends on how it was deployed**, and the panel must not
   pretend otherwise. Detect and act accordingly:
   - **Docker/Compose** (the default path for other people): the panel *cannot*
     replace its own container. Correct behaviour is to surface "v0.4.1 available"
     with the exact `docker compose pull && up -d` to run, plus the changelog. A
     panel that tries to `docker exec` its way out of its own container is a
     footgun and a privilege-escalation surface — don't.
     Optionally support a **watchtower-style sidecar** as a documented opt-in.
   - **NixOS module**: updates come from the flake input. The panel should say so
     and link the changelog; `nixos-rebuild` is the operator's job. Do NOT shell out.
   - **pip/venv/systemd**: this is the only one where an in-place self-update is
     honest. `POST /api/update` (admin + CSRF + typed-confirm) → verify the release
     signature/checksum → `pip install --upgrade` into a *new* venv → run DB
     migrations → `systemctl restart` → health-check → **roll back the symlink if
     the new version fails its health check.** Never upgrade in place over a
     running venv.
3. **Migrations.** There is no migration system today (`db.py` uses
   `CREATE TABLE IF NOT EXISTS`). Before self-update is safe, add a `schema_version`
   row + ordered migration steps, and **back up the sqlite file before applying**
   (`hlidskjalf.sqlite3.bak-<version>`). The `secret.key` must be preserved across
   updates or every stored secret is orphaned — call this out loudly in the docs.
4. **Security.** `/api/update` is remote code execution by design. It must be:
   admin-only, CSRF-guarded, typed-confirmation, rate-limited, **off unless
   `HLIDSKJALF_ALLOW_SELF_UPDATE=true`**, and it must verify the artifact
   (checksum from the release, ideally a signature) before running anything.
   Default OFF. An unauthenticated or sloppy update endpoint is a worse hole than
   anything the v0.3.6 audit found.
5. **UI.** A quiet "update available" chip in the sidebar footer → a Settings/About
   page with current version, latest version, changelog, and either the copy-paste
   command (docker/nix) or the Update button (venv, if enabled).

**Do first:** the `schema_version` + migration + backup work (3). Self-update on top
of an unversioned schema is how people lose their bandwidth history.

### Known gaps / next up
1. **The switch faceplate is still hardcoded** to a 48-port + 4-QSFP Arista
   DCS-7050TX-48 (`Switch.tsx` renders `Ethernet1..52` regardless of what the switch
   reports). For genericity it should render from the ports the backend actually
   returns, with the model read from eAPI (`show version` → `modelName`). The Switch
   page is already optional (unset `switch_host` hides it).
2. **Real-hardware validation — Phase 1 DONE (2026-07-13), Phases 2–4 remain.** The
   read-only validator passed against the real host (PVE 9.2.3): 35 pass, 0 FAIL — see the
   "START HERE" section at the top for full results and environment facts. The panel
   process itself has still never run against real Proxmox; that is Phase 2. The
   manual checklist in **`docs/real-hardware-validation.md`** (open a console and
   *type* in it; watch a task poll complete as a regular user; protected destroy
   refusal) is still all ahead of us. The UPID and mock-disk lies are fixed
   (PRs #32–#34 + the earlier 9-field UPID fix).
3. Prometheus exporter metric names are assumed from prometheus-pve-exporter's
   `/cluster/resources` collector — confirm against your exporter version. Node
   `iowait`/`loadavg`/`netin`/`netout` don't exist there and stay null unless you set
   `HLIDSKJALF_PROMETHEUS_NODE_QUERIES` (documented).
4. Branch protection still needs GitHub Pro on a private repo (unchanged).

## Previous state — v0.3.5-alpha (frontend design system)

Branch `feat/frontend-design-v0.3.5-alpha` (PR pending/merged). **Frontend only.**
A deliberate design-system pass using the `frontend-design` skill, grounded in the
subject (Hlidskjalf = the high seat watching every guest on host "pve").

- **Type concept**: added **Archivo Variable** (`@fontsource-variable/archivo`,
  weight + width axes) as `font-sans`/`font-display` for the human interface;
  **JetBrains Mono** is now reserved strictly for machine data via the `.metric`
  token. The mono-vs-sans split is the design thesis. `main.tsx` imports Archivo
  `standard.css` (gives wght 100–900 + wdth 62–125%).
- **Foundation (built by me, LOCKED)**: `tailwind.config.js` (2 surface levels +
  `abyss`, `surface-2`, brighter fg/muted, kept accent hexes, `tracking-eyebrow`),
  `src/index.css` (ambient aurora bg, `.card` elevation, `.well` recessed panels,
  `.eyebrow`, `.wordmark`, `.reveal` load anim, focus-visible, reduced-motion),
  `components/ui.tsx` (new `<PageHeader>`, restyled Card/states/StatusDot/etc.),
  `components/Layout.tsx` (high-seat masthead rail + aurora nav), `pages/Login.tsx`
  (hero + thesis copy). Design spec archived at `docs/design/v0.3.5-design-system.md`.
- **Pages** brought onto the system in three parallel passes (disjoint file sets;
  Fleet+Node, Provision+Users, Debug+VmDetail+vm/*) + Switch by me. Each page opens
  with `<PageHeader eyebrow title>`; data in mono; cards/wells; disciplined accents
  (cyan=live, pink=brand/selection only, amber=attention, red=danger). Behavior,
  props, API contracts all unchanged. `tsc` + `vite build` clean.
- **Docs**: gallery `docs/screenshots/v0.3.5-alpha/` (10 live captures) + README +
  capture.js; index/main READMEs point at it; CHANGELOG v0.3.5-alpha; version bump.
- **Dev-stack note** (same as v0.3.4): mock_switch must run with TLS + the backend
  needs the pinned fingerprint, `HLIDSKJALF_COOKIE_SECURE=false` for http dev. There
  is a launcher at `scratchpad/run_backend.sh` pattern; exact commands below.

## Previous state — v0.3.4-alpha (frontend-only pass)

Branch `feat/frontend-v0.3.4-alpha` (PR pending merge at time of writing; if you
see this on `main` it merged). **Frontend only** per Christian's request:

- **Robustness/security**: reusable `ErrorBoundary` (app-level + per-page reset-on-nav
  + faceplate), Users page rewritten (no `prompt()` — modals for assign/reset-pw/
  **delete user**, role select, validation, retry state), api.ts 20 s AbortController
  timeout + network-vs-timeout errors, `/vm/:vmid` param validation, `watchTask`
  15-min cap, toast cap (5), ConfirmDialog Escape/Enter + aria, login input
  hardening, `referrer no-referrer` + `noindex` metas, hidden admin-only danger
  zone for regular users (server already enforced 403; UI showed dead buttons).
- **Visual fix**: deduped a legacy CSS block that was squashing the switch faceplate
  ports into the chassis corner — ports now span full width (2×24), verified
  against v0.3.2 screenshots side by side.
- **Design**: login redesign (gradient accent + glow), sidebar accent nav + role
  badges + tagline, focus-visible rings, spinner LoadingState, reduced-motion
  support, button/input transitions.
- **Docs**: `docs/screenshots/v0.3.4-alpha/` gallery (10 live captures incl. debug
  page + login + both roles) with README + generic capture.js; CHANGELOG v0.3.4-alpha
  section; main/screenshots READMEs point at v0.3.4-alpha; frontend version bumped.
- Verified: `tsc --noEmit` + `npm run build` clean; backend suite untouched (98
  passing on main); full dev-stack visual pass in Chromium (screenshots ARE that pass).
- **Dev-stack gotcha discovered**: since PR #18 the switch eAPI client verifies TLS,
  so `mock_switch` must run with its TLS cert and the backend needs the pin:
  `uvicorn mock_switch:app --port 18080 --ssl-certfile mock_switch.crt --ssl-keyfile mock_switch.key`
  and `HLIDSKJALF_SWITCH_FINGERPRINT=$(openssl x509 -in dev/mock_switch.crt -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')`.
  Also `HLIDSKJALF_COOKIE_SECURE=false` for http dev. Screenshot gallery for the
  never-captured v0.3.3.3 is superseded by v0.3.4-alpha.

## Previous state — security hardening batch landed

`main` (post-merge) is GREEN: **98 backend tests pass**, `tsc`/`build` clean. Merged this batch:
- **PR #16** — fixed a CSRF bug that made every authenticated mutation 403 (and had been merged RED, 16 failing tests). One-line fix; suite restored.
- **PR #17** — closed console IDOR + rescue broken-access-control (+ protected-VMID guard on rescue). The rescue hole let any user reboot any VM incl. panel-host (hosts the panel).
- **PR #18** — hardening: no traceback leak to clients; `Secure` cookie (`HLIDSKJALF_COOKIE_SECURE`, default true); switch eAPI TLS verify/pin; legacy env-admin login only during bootstrap; per-IP login rate limit.
- **PR #19** — first authz test coverage + users 404/last-admin fixes + removed dup `get_status`.
- Post-merge test-only fix on main: `test_authz_scoping` used vmid 105 which `test_access_control` already assigns in the session-scoped DB → moved it to vmid 120 (they collided only when run together).

### Still TODO (next session)
1. ~~Screenshots gallery~~ **DONE** — `docs/screenshots/v0.3.4-alpha/` supersedes the never-captured v0.3.3.3 gallery.
2. **Branch protection is NOT available** on this private repo without GitHub Pro — both the classic branch-protection API and the Rulesets API return "Upgrade to GitHub Pro or make this repository public." Options: pay for Pro, make the repo public (NOT recommended — plan.md exposes homelab IPs/hostnames), or keep the current discipline (I verify local pytest green + scope before every merge). Nothing was applied.
3. Stale remote branches to prune (all merged/abandoned): `feat/switch-*` (many), `feat/debug-section`, `feat/normalize-pve-shapes`, `feat/v0.3.2-alpha-multi-user`.
4. Optional follow-ups: `/api/tasks/{upid}/status` is unscoped (low-sensitivity IDOR — backend, deferred from the frontend-only session); ~~frontend robustness pass~~ **DONE in v0.3.4-alpha**; consider code-splitting recharts (main JS chunk is ~630 kB min / 183 kB gz).

## Previous state (v0.3.2-alpha)

## ⚡ Current state — v0.3.2-alpha (Multi-user Admin + User panels landed)

**Major release**: Transformed from single-admin homelab panel into a flexible VPS-style control surface that can be shipped remotely with minimal config.

- **Admin panel**: Full fleet, provision new VMs, Users management (create/assign/reset), node, complete switch activity + notes.
- **User panel**: Each non-admin gets **exactly one VM**. Auto-redirect home, scoped views only for their VM (power, graphs, bandwidth/quotas, console, tasks, rescue). Can still see Switch for activity context.
- Backend: users table, bootstrap from legacy admin hash, strict role + vmid scoping on every route.
- Frontend: role-driven navigation, Layout shows user + role, dedicated Users.tsx admin page, updated App routing.
- "Out of the box": first run seeds admin; everything else (add customers + assign VMs) is UI driven. Same PVE token + switch eAPI model.
- Dev stack fully verified post-changes (mocks + backend + vite). Login flows tested for both roles.
- Version bumped everywhere (pyproject 0.3.2-alpha, package.json, docs).
- New gallery: `docs/screenshots/v0.3.2-alpha/` with live captures of admin fleet/users/provision + user "My VM" + switch.

**Screenshots captured** via puppeteer against live 5173 (admin + demo user):
- admin-fleet.png, admin-users.png, admin-provision.png, admin-switch.png
- user-my-vm.png, user-switch.png

All prior switch faceplate / eAPI / LLDP work preserved and now available in both panels.

See new `docs/screenshots/v0.3.2-alpha/README.md` and updated CHANGELOG + main README.

## Debug / Logging & Error Handling (v0.3.2+ work)

- **Deployed sub-agents**:
  - Backend: implemented enhanced logging, request middleware, global error handler, in-memory buffers, debug router.
  - Frontend: built full Debug.tsx admin page + api.ts helpers + nav integration.
- Backend improvements:
  - `log_level` (DEBUG/INFO/...) + `debug` flag from env/settings.
  - HTTP request logging middleware (logs every call with timing).
  - Global Exception handler: always logs full traceback at ERROR; in debug mode returns traceback snippet to client.
  - New `routes/debug.py` with 5 admin-protected endpoints.
  - In-memory recent_logs + recent_errors (100 cap) populated automatically.
- Frontend Debug section (`/debug`, admin nav only):
  - Live System Health, redacted Config, Accumulator, Recent Logs (color coded levels), Recent Errors (expandable tracebacks).
  - usePoll auto-refresh + manual refresh buttons.
  - Fully gated; graceful on 401/empty.
- Usage: set `HLIDSKJALF_DEBUG=true HLIDSKJALF_LOG_LEVEL=DEBUG` then login as admin and visit /debug.
- Fully documented in CHANGELOG.md + this handoff.md.
- Verified: tsc build clean, dev stack works, imports OK, routes mounted (401 until authed as admin).

## Previous releases (archived)

### v0.3.1-alpha (React faceplate) and prior

(History from PRs #1–#4 and earlier faceplate work follows; design source of truth remains `plan.md`.)

## ⚡ Archived PR history (v0.3.1 and prior)

`main` now contains PRs #1–#4 (all merged, branches deleted). Verified on the
merged tree: **50 pytest pass**, `tsc --noEmit` + `npm run build` clean.

- **PR #1 `feat/tests-ci`** — pytest suite (auth/CSRF/rate-limit, vms, safety
  rails, provision, rescue, accumulator, bandwidth, TLS pinning) + `[test]` extra
  + `.github/workflows/ci.yml` (backend pytest + frontend tsc/build).
- **PR #2 `test/console-ws`** — mock `vncwebsocket` echo endpoint + 6 integration
  tests for the noVNC WS proxy (the previously-untested flow). Fixed a real bug:
  `routes/console.py` closed the socket *before* `accept()`, so noVNC never
  received the 4401/4403 close codes — now accepts first, then closes with code.
- **PR #3 `deploy/docker`** — multi-stage Dockerfile + compose + `hlidskjalf.env.example`
  + `docs/docker.md` + build-only `.github/workflows/docker.yml`. ~225 MB image,
  smoke-tested to `healthy` (health + login) without real Proxmox. Non-Nix path
  for a plain Debian VM. Gotchas in docs: `CMD` not `ENTRYPOINT` (override
  support); single-quote the argon2 hash in compose `env_file` (`$`-interp),
  unquoted for `docker run --env-file`.
- **PR #4 `fix/ui-visual-pass`** — real in-browser pass (system Chromium 150 via
  puppeteer-core). Fixed 4 defects: console tab stuck loading (`VmDetail.tsx`),
  node RAM "— / —" (`NodePage.tsx` — PVE nests `status.memory`), completed tasks
  shown red (`TasksTab.tsx` — result is in `exitstatus`), 50/50 bandwidth bar on
  zero-traffic VMs (`OverviewTab.tsx`). Added `docs/screenshots/*` + README
  `## Screenshots`.

**Flagged backend follow-ups (from PR #4, worked around in the frontend, not yet
fixed):** resolved by this PR (see below). Previously: `/api/node` returned raw
PVE shape (nested `memory`/`rootfs`, cores in `cpuinfo.cpus`, no flat `maxcpu`)
and `/api/tasks/recent` passed tasks verbatim (`status` vs `exitstatus`).
The frontend tolerated both; backend now normalizes for consistency.

**GitHub API access:** a fine-grained PAT (repo `jivsan/Hlidskjalf`, push/PR,
expires 2026-08-11, but pasted in chat once → rotate it) is stored at
`~/.hlidskjalf_gh_token` (0600). It can create/merge PRs and read repo/PR data
but LACKS Actions:read, so CI status can't be queried via API — read it on the
PR page, or rely on local `pytest`/`tsc`/`build`. Push still uses SSH.

## Name

The project is **Hlidskjalf** (Odin's high seat), renamed from "bifrost"
throughout `plan.md` and all code. Env prefix `HLIDSKJALF_`, PVE user
`hlidskjalf@pve!panel`, CSRF header `X-Hlidskjalf-CSRF`, service
`services.hlidskjalf`, suggested host `hlidskjalf.oryxserver.org`.

## State: what exists and is verified

### Backend (`backend/hlidskjalf/`) — DONE, smoke-tested end-to-end against the mock

- `config.py` — pydantic-settings, all env-driven. Gotchas already handled:
  `protected_vmids` uses `NoDecode` (comma-separated env), quotas/gateways are
  JSON env vars (quote them in shell-sourced env files!).
- `pve.py` — async httpx client; TLS pinned by SHA-256 cert digest enforced
  *inside the handshake* (`SSLContext.sslobject_class` override) so the same
  context also pins the websocket VNC connection. Refuses to start over https
  without a fingerprint. `wait_task()` polls UPIDs.
- `db.py` — aiosqlite: `bandwidth(vmid,date)` daily rows, `counters` baselines,
  `rescue` boot-order stash.
- `accumulator.py` — 60 s loop, counter-reset rule, persists baselines each
  cycle. **Verified: panel restart does not double-count** (delta stayed sane
  across restart in test).
- `auth.py` — argon2 verify, HttpOnly SameSite=Strict signed cookie
  (itsdangerous), CSRF = HMAC(session value), login rate-limit 5/min.
- `routes/` — vms (list/detail/power + agent IPs), metrics (VM + node rrddata
  via `datasources/rrd.py`), bandwidth (range/monthly/summary + quotas),
  provision (create/reinstall/destroy — name-confirm + protected-VMID guards,
  `firewall=0` hardcoded in `_net0()`), rescue (enter/exit, stash in sqlite,
  free-ide-slot picking, stop+start not reboot), console (vncproxy ticket +
  WS byte pump at `/ws/console/{vmid}?key=`; one-time key + session cookie).
- `main.py` — lifespan wiring, `/api/login|logout|session|health`, SPA static
  serving with fallback, `hlidskjalf` console script (`run()`).
- All verified flows: login/CSRF/rate-limit, fleet, detail (agent IPs), metrics,
  node+storage, templates, provision→config checks, rescue enter/exit (boot
  restored, ISO detached), reinstall (MAC/IP preserved), destroy (wrong name
  400s, protected 403s), bandwidth summary/range/monthly with quota utilization.
  **Console WS pump is the one path not exercised** (mock has no real VNC).

### Mock PVE (`dev/mock_pve.py`) — DONE

Fake PVE on :18006 (plain http): resources/status/config/rrddata/tasks/clone/
resize/destroy/power/agent/vncproxy, synthetic traffic counters that tick, so
the accumulator books real-looking data. Needs `python-multipart` in the venv
(form parsing). `dev/dev.env` (gitignored) holds a working dev config —
recreate via README "Local development"; login is admin/devpass.

### Frontend (`frontend/`) — source complete, build verified, served by backend

Vite + React 18 + TS + Tailwind v3 + Recharts + @novnc/novnc +
@fontsource/jetbrains-mono. All pages/tabs per plan §5: Login, Fleet, VmDetail
(Overview/Graphs/Console/Rescue/Tasks), Provision, Node; api.ts wraps CSRF and
401-redirect (contract spot-checked against the backend). `npx tsc --noEmit`
and `npm run build` pass clean; the built `dist/` is served by the backend
(index, assets, SPA fallback, path-traversal guard all verified with curl).
**Not yet done: an actual in-browser visual pass** — no Chromium on this box.
Easiest: `ssh -L 8787:127.0.0.1:8787 hermes-agent`, start mock+backend per the
cheat-sheet below, open http://127.0.0.1:8787 (admin/devpass).

### Nix (`nix/`) + docs — WRITTEN, NOT BUILT (no nix on this box)

- `flake.nix` (packages + devShell + nixosModules), `nix/package.nix`
  (buildNpmPackage → buildPythonApplication, wrapper bakes STATIC_DIR),
  `nix/module.nix` (DynamicUser, StateDirectory=hlidskjalf, hardening,
  EnvironmentFile secrets). **`npmDepsHash = lib.fakeHash` placeholder — set the
  real hash on first `nix build` failure output.**
- `docs/bootstrap.md` — manual pve steps (token/ACLs with the plan's
  storage/local typo corrected to PVEDatastoreUser, template, ISO, argon2 hash
  command, Traefik snippet). `README.md` — dev + deploy quickstart.

## Immediate next steps (in order)

1. ~~Optional small PR: normalize...~~ **DONE in this PR** (`feat/normalize-pve-shapes`).
2. On a nix machine: `nix build .#hlidskjalf` → fix `npmDepsHash`, then
   `nix flake check`.
4. Real deployment (manual). Two paths now:
   - **Nix (primary):** `docs/bootstrap.md` on the Proxmox host → secrets env on
     panel-host → flake input + Traefik + DNS in dotfiles (plan §7).
   - **Docker (any Debian VM):** `docs/docker.md` (after PR #3 merges).
5. M2–M4 acceptance against real pve with **scratch VMIDs ≥ 900 only**;
   confirm plan §10 open items (real storage IDs, real protected VMIDs —
   panel-host/hermes-agent/HAOS VMIDs still unknown, VLAN 30 gateway).

## Git / PR workflow

Pushed to git@github.com:jivsan/Hlidskjalf.git (SSH key
`~/.ssh/id_ed25519_github`, wired via `core.sshCommand`). Author identity for
this repo: `jivsan <chrsol3@gmail.com>` — GitHub username, no full name, no
co-author trailers. Branches: `main` (everything through the Nix/docs work),
`feat/tests-ci` (WIP, see top). No GitHub API token and no `gh` on this box —
PRs can't be created programmatically; push the branch and open
`https://github.com/jivsan/Hlidskjalf/pull/new/<branch>` (the link 404s until
the branch actually has pushed commits). To enable real PR creation: install
`gh` and `gh auth login` with a PAT, or export GITHUB_TOKEN.

## Open decisions / deferred

- Hosting: recommendation is **panel-host** (existing Traefik + wildcard cert +
  NixOS module deploy). NOT on the Proxmox host itself (keep the hypervisor
  clean; the whole security model is a scoped token from a separate machine).
  A small Debian VM works too (venv + systemd + env file) if panel-host is out.
- rrddata seeding of first-month bandwidth: nice-to-have, skipped.
- Prometheus datasource: Phase 2 stub in `datasources/prometheus.py`.
- LXC: list/detail/power work; provisioning is qemu-only (per plan non-goals).

## Dev loop cheat-sheet

```bash
.venv/bin/uvicorn mock_pve:app --port 18006             # from dev/
set -a; source ../dev/dev.env; set +a                    # from backend/
../.venv/bin/uvicorn hlidskjalf.main:app --port 8787     #   (login admin/devpass)
npm run dev                                              # from frontend/, :5173 proxies to :8787
```
