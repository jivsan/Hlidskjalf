# handoff.md — Hlidskjalf build status

_Last updated: 2026-07-12. Purpose: pick up exactly here when a session dies or
limits hit. The design source of truth is `plan.md`; this file is only "what is
done / what's next"._

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

1. Visual pass in a real browser against the mock (see Frontend section above);
   fix whatever looks broken (esp. Recharts sizing, empty states, bandwidth
   charts, provision task log).
2. On a nix machine: `nix build .#hlidskjalf` → fix `npmDepsHash`, then
   `nix flake check`.
3. Real deployment (Christina, manual): `docs/bootstrap.md` on hella → secrets
   env on heimdall → flake input + Traefik + DNS in dotfiles (plan §7).
4. M2–M4 acceptance against real hella with **scratch VMIDs ≥ 900 only**;
   confirm plan §10 open items (real storage IDs, real protected VMIDs —
   heimdall/hermes-agent/HAOS VMIDs still unknown, VLAN 30 gateway).

## Git

Committed on `main`, pushed to git@github.com:jivsan/Hlidskjalf.git (SSH key
`~/.ssh/id_ed25519_github`, wired via `core.sshCommand`). Author identity for
this repo: `jivsan <chrsol3@gmail.com>` — no co-author trailers.

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
