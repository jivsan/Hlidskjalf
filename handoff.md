# handoff.md — Hlidskjalf build status

_Last updated: 2026-07-12 (v0.3.2-alpha release — multi-user admin/user panels, VPS model, scoped one-VM-per-user, full docs + screenshots). The design source of truth is `plan.md`; this file is only "what is done / what's next"._

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
  - Backend subagent: implemented enhanced logging, request middleware, global error handler, in-memory buffers, debug router.
  - Frontend subagent: built full Debug.tsx admin page + api.ts helpers + nav integration.
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
recreate via README "Local development"; login is christina/devpass.

### Frontend (`frontend/`) — source complete, build verified, served by backend

Vite + React 18 + TS + Tailwind v3 + Recharts + @novnc/novnc +
@fontsource/jetbrains-mono. All pages/tabs per plan §5: Login, Fleet, VmDetail
(Overview/Graphs/Console/Rescue/Tasks), Provision, Node; api.ts wraps CSRF and
401-redirect (contract spot-checked against the backend). `npx tsc --noEmit`
and `npm run build` pass clean; the built `dist/` is served by the backend
(index, assets, SPA fallback, path-traversal guard all verified with curl).
**Not yet done: an actual in-browser visual pass** — no Chromium on this box.
Easiest: `ssh -L 8787:127.0.0.1:8787 hermes-agent`, start mock+backend per the
cheat-sheet below, open http://127.0.0.1:8787 (christina/devpass).

### Nix (`nix/`) + docs — WRITTEN, NOT BUILT (no nix on this box)

- `flake.nix` (packages + devShell + nixosModules), `nix/package.nix`
  (buildNpmPackage → buildPythonApplication, wrapper bakes STATIC_DIR),
  `nix/module.nix` (DynamicUser, StateDirectory=hlidskjalf, hardening,
  EnvironmentFile secrets). **`npmDepsHash = lib.fakeHash` placeholder — set the
  real hash on first `nix build` failure output.**
- `docs/bootstrap.md` — manual hella steps (token/ACLs with the plan's
  storage/local typo corrected to PVEDatastoreUser, template, ISO, argon2 hash
  command, Traefik snippet). `README.md` — dev + deploy quickstart.

## Immediate next steps (in order)

1. ~~Optional small PR: normalize...~~ **DONE in this PR** (`feat/normalize-pve-shapes`).
2. On a nix machine: `nix build .#hlidskjalf` → fix `npmDepsHash`, then
   `nix flake check`.
4. Real deployment (Christina, manual). Two paths now:
   - **Nix/heimdall (primary):** `docs/bootstrap.md` on hella → secrets env on
     heimdall → flake input + Traefik + DNS in dotfiles (plan §7).
   - **Docker (any Debian VM):** `docs/docker.md` (after PR #3 merges).
5. M2–M4 acceptance against real hella with **scratch VMIDs ≥ 900 only**;
   confirm plan §10 open items (real storage IDs, real protected VMIDs —
   heimdall/hermes-agent/HAOS VMIDs still unknown, VLAN 30 gateway).

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

- Hosting: recommendation is **heimdall** (existing Traefik + wildcard cert +
  NixOS module deploy). NOT on the Proxmox host itself (keep the hypervisor
  clean; the whole security model is a scoped token from a separate machine).
  A small Debian VM works too (venv + systemd + env file) if heimdall is out.
- rrddata seeding of first-month bandwidth: nice-to-have, skipped.
- Prometheus datasource: Phase 2 stub in `datasources/prometheus.py`.
- LXC: list/detail/power work; provisioning is qemu-only (per plan non-goals).

## Dev loop cheat-sheet

```bash
.venv/bin/uvicorn mock_pve:app --port 18006             # from dev/
set -a; source ../dev/dev.env; set +a                    # from backend/
../.venv/bin/uvicorn hlidskjalf.main:app --port 8787     #   (login christina/devpass)
npm run dev                                              # from frontend/, :5173 proxies to :8787
```
