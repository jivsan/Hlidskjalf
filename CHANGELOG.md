# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Prometheus metrics datasource** (plan.md §8, phase 2): `datasources/prometheus.py`
  now implements the `MetricsSource` protocol against heimdall's Prometheus HTTP API
  (`/api/v1/query_range`) with prometheus-pve-exporter's series, as a drop-in
  alternative to rrddata — `HLIDSKJALF_METRICS_SOURCE=prometheus` (`rrd` stays the
  default; a deployment that sets none of the new vars is unchanged). Same row shapes,
  same timeframe windows, but finer long-range steps (month 12h → 1h, year 1w → 1d),
  which was the whole point. Counters (`netin`/`netout`/`diskread`/`diskwrite`) are
  `rate()`d back into the bytes/sec rrddata reports; gauges are consolidated over the
  step (`avg_over_time` / `max_over_time` for `cf=AVERAGE|MAX`). Prometheus being down
  degrades to `null` fields / empty series instead of failing the metrics endpoint.
  New env: `HLIDSKJALF_PROMETHEUS_URL` (+ optional token / basic auth / TLS pinning /
  timeout / `HLIDSKJALF_PROMETHEUS_NODE_QUERIES`). See `docs/prometheus.md`.
- `dev/mock_prometheus.py` — offline mock of the Prometheus HTTP API for dev + tests.

## [v0.3.5-alpha] - 2026-07-13

### Design (frontend-only visual system pass)
A deliberate design-system pass giving the panel a distinctive identity built
around its subject — Hlidskjalf, the high seat from which one watches every guest
running on the host "hella".

- **Type system — the concept**: introduced **Archivo** (variable, weight + width
  axes) as the human interface face for headings, nav, labels, and the wordmark,
  and reserved **JetBrains Mono** strictly for machine data (metrics, IDs, UPIDs,
  MACs, IPs, byte counts, the faceplate). The mono-vs-sans split now *means*
  something instead of being mono-everywhere by default.
- **Signature masthead**: the wordmark is set wide + heavy (Archivo 800 / width
  125%) so the name reads as carved into the seat; the sidebar became a proper
  identity rail — wordmark, hairline, "high seat · hella" with a live pulse, an
  aurora-bar active nav, and a "leave the seat / take the seat" vocabulary.
- **Login redesigned** as a quiet hero: monumental wordmark, a one-line thesis
  ("The high seat. From it, one watches over every realm running on hella."), and
  a disciplined form with a single orchestrated page-load reveal.
- **Refined palette**: deepened the night background, split surfaces into two
  elevation levels (`surface` / `surface-2`) plus an `abyss` for recessed data
  wells, brightened `fg`/`muted` for legibility. Accent hues (cyan/pink/amber/red)
  kept exactly, but disciplined — cyan = live/healthy, pink = brand + selection
  only, amber = attention, red = danger. No more pink-everywhere.
- **Shared component kit**: new `<PageHeader>` (eyebrow + display title) opens every
  page on one rhythm; `Card`, `.well` (recessed data panels), `.eyebrow` (labeled
  hairline), `LoadingState` (spinner), `StatusDot` (live ping), `ProgressBar`
  restyled. Ambient aurora background, `.reveal` load animation, `:focus-visible`
  rings, and `prefers-reduced-motion` all handled centrally.
- **Every page** (Fleet, Node, Provision, Users, Debug, Switch, VM detail + tabs)
  brought onto the system: consistent headers, data in mono, cards and wells,
  quiet accents. Behavior, props, and API contracts unchanged.
- New gallery `docs/screenshots/v0.3.5-alpha/`. Frontend version bumped to
  0.3.5-alpha; `tsc` + `vite build` clean.

## [v0.3.4-alpha] - 2026-07-13

### Frontend robustness + security (frontend-only pass)
- **Error boundaries**: new reusable `ErrorBoundary` component wraps the whole app
  and every routed page (resets on navigation) — a render crash on one page no
  longer blanks the entire SPA. The switch faceplate uses it too (replacing its
  local one-off boundary).
- **Users admin page rewritten**: browser `prompt()` dialogs are gone (passwords
  were typed into plaintext prompts and API failures were silently unhandled).
  Now: proper modals for assign-VM / reset-password / **delete user** (typed-name
  confirm), role select (admin/user), free-VM filtering in assign dropdowns,
  client-side validation (username charset, min password length), load-error
  state with retry, VM names shown next to assigned vmids.
- **API layer**: 20 s request timeout via `AbortController` (a hung backend can no
  longer wedge spinners forever), distinct "network error / timed out" messages,
  tolerant of empty response bodies.
- **Role-aware danger zone**: reinstall/destroy controls are hidden from regular
  users (they are admin-only server-side; the UI showed dead buttons that 403'd).
- **Defensive rendering**: `/vm/:vmid` validates the URL param before issuing API
  calls; `watchTask` gives up after 15 min instead of polling forever; toast stack
  capped at 5; Debug page renders nested objects as JSON (was `[object Object]`);
  ConfirmDialog closes on Escape, confirms on Enter, and is `aria-label`led.
- **Login hardening**: username trimmed, autocapitalize/autocorrect/spellcheck off.
- `index.html`: `referrer no-referrer` + `robots noindex` meta tags.
- Removed the hardcoded switch management IP from the Switch page header.

### Fixed (visual)
- **Switch faceplate layout**: a duplicated legacy CSS block was overriding the
  responsive grid with fixed 11-px flex ports, squashing all 48 ports into the
  upper-left corner of the chassis. Deduped — ports now span the full faceplate
  in 2×24 rows with port numbers, matching the real DCS-7050TX-48.

### Changed (design)
- Login page redesigned: gradient accent card, radial backdrop glow, larger
  wordmark, footer tagline.
- Sidebar: accent-bar active nav states, hover transitions, role badge
  (pink admin / cyan user), "HIGH SEAT · HELLA" tagline, username truncation.
- Buttons/inputs: subtle color transitions; keyboard `:focus-visible` ring
  everywhere; spinner-based `LoadingState`; `prefers-reduced-motion` disables
  LED blink animations.
- New screenshot gallery `docs/screenshots/v0.3.4-alpha/` (login, admin
  fleet/users/provision/node/switch/debug/vm-detail + user my-vm/switch),
  captured live from the dev stack. READMEs updated to point at it.
- Frontend version bumped to 0.3.4-alpha. `tsc` + `vite build` clean.

### Security (hardening batch — PRs #16–#19)
- **Fixed CSRF login token that broke every mutation** (PR #16): `start_session`
  handed out `csrf_for(signed_cookie)` while `require_csrf`/`/api/session` expect
  `csrf_for(username)`, so all authenticated POST/PUT/DELETE got 403. Also greened
  16 failing tests that had been merged red.
- **Console IDOR + rescue broken-access-control** (PR #17): `GET /api/vms/{vmid}/console`
  was unscoped (any user could open any tenant's VNC console); rescue enter/exit had
  no ownership/admin check and no protected-VMID guard (any user could reboot any VM
  into the rescue ISO, incl. heimdall which hosts the panel). Both now enforce
  `_ensure_vm_access`; rescue-enter also refuses protected VMIDs for everyone.
- **Hardening** (PR #18): exception handler no longer leaks tracebacks/`error_type`
  to clients (kept in logs + admin `/api/debug/errors`); session cookie `Secure`
  now configurable (`HLIDSKJALF_COOKIE_SECURE`, default true); switch eAPI TLS is
  verified/pinnable (`switch_fingerprint`/`switch_verify`) instead of `verify=False`;
  env admin-hash login only works during fresh bootstrap (no permanent backdoor);
  login rate limit is now per-client-IP (was one global bucket → trivial lockout DoS).
- **Authz test coverage + robustness** (PR #19): first real tests of the multi-user
  model (per-VM scoping + admin-only guards); `users` endpoints 404 on missing target
  and refuse deleting the last admin; removed a duplicated `get_status`.
- Full backend suite: **98 passing**.

### Added / Changed (Debug section)
- **Debug section** (admin-only, gated by `HLIDSKJALF_DEBUG=true`):
  - New `/api/debug/*` endpoints: `/config` (redacted), `/health` (detailed), `/errors`, `/logs`, `/accumulator`.
  - Request logging middleware (method/path/status/duration/client).
  - Global exception handler with full tracebacks (ERROR level) + debug-mode traceback snippets in responses + in-memory error buffer.
  - In-memory log buffer via custom logging.Handler for recent logs.
  - Configurable `log_level` via settings.
- New admin **Debug** page (`/debug`): System Health, redacted Config, Accumulator status, Recent Logs (color-coded), Recent Errors (with expandable tracebacks). Polling + manual refresh. Consistent cyberpunk styling.
- Only visible in admin nav when role=admin.
- Documented in handoff.md + this changelog.

## [v0.3.2-alpha] - 2026-07-12

### Added / Changed
- **Multi-user support with Admin + User panels (VPS model)**:
  - Regular users each tied to **exactly one VM** (like a VPS customer).
  - Scoped access: users can only see/control their assigned VM (power, graphs, bandwidth, console, rescue, tasks).
  - Users can view Switch activity page (for LLDP/rates/top-talkers context) but cannot edit notes.
  - Admins get full Fleet, Provision, Node, user management, and global views.
- New **Users admin page** (`/users`): create users, assign existing VM, reset passwords. Enforces one-VM-per-user.
- Role-aware frontend: dynamic nav, home redirect for users to their VM, role badge in sidebar.
- Backend: users table in SQLite, bootstrap from legacy admin env on first run, `get_current_user`, strict ownership checks on all per-VM and admin-only routes.
- New endpoints: `/api/me`, `/api/users` (list/create/assign/password), richer `/api/login` + `/api/session` responses with `role` + `vmid`.
- All existing features (eAPI switch, LLDP, bandwidth accounting/quotas, noVNC, etc.) now respect roles.
- Improved "out of the box" for remote shipping: minimal config, UI-driven user/VM assignment after initial PVE token + switch setup.
- Version bumped to 0.3.2-alpha. New screenshot gallery in `docs/screenshots/v0.3.2-alpha/`.
- Updated handoff.md, READMEs, and docs to reflect the new admin/user experience.

### Technical
- DB schema extension + user CRUD + unique vmid index (non-null).
- Refactored auth + every route (vms, provision, bandwidth, metrics, switch notes, rescue, console) for admin guards + user VM scoping.
- Frontend: App.tsx role routing, Layout dynamic nav, new Users.tsx page, updated types/api.
- Dev stack continues to work (mocks auto-seed data).

See `docs/screenshots/v0.3.2-alpha/README.md` for visual comparison (admin fleet/users vs user single-VM view).

## [v0.3.1-alpha] - 2026-07-12

### Added / Changed
- **Finalized realistic React+CSS faceplate** for Arista DCS-7050TX-48 on `/switch`:
  - Pure declarative React (buttons + divs for ports, no Canvas, no SVG).
  - Matches physical 1U hardware: rack ears with screws, vents, dark metal chassis bevels/gradients, exact labels, recessed RJ45 jacks (latch notch, 8 pins), LEDs above every port, 4 QSFP cages with lanes + 40G badge.
  - 48 RJ45 (2 grids of 24) + 4 QSFP right. Full click-to-select, hover, LLDP titles, activity blink (CSS only).
  - Fixed hooks order (moved useState before any conditional return) + DOM structure to match CSS selectors.
  - New screenshots captured live from dev stack, placed in `docs/screenshots/v0.3.1-alpha/`.
- Named release v0.3.1-alpha per request; updated all pointers (READMEs, screenshots index, this changelog).
- Stack runs in dev (mocks + backend + vite) for viewing changes.
- PRs/branches coordinated and changes merged into feat/switch-react-faceplate (local + prior API merges).



### Changed
- **Switch faceplate refactored to declarative React components** (divs, buttons + Tailwind/CSS, no Canvas/SVG):
  - Pure React: `<Rj45Port>` and `<QsfpPort>` small components receiving name/status/active/selected/lldpNeighbor/onClick/onHover.
  - Realistic physical 1U Arista DCS-7050TX-48: multi-layer dark metal chassis gradients + bevels/shadows, rack ears w/ screws, top/bottom vents (repeating slots), left mgmt (CON/USB/MGMT) + static status LEDs (SYS/FAN/PS), model labels exact.
  - RJ45: recessed jack body w/ latch notch (::before), 8 contact pins (flex spans), LED absolutely positioned above with specular + CSS blink animation on .active.
  - QSFP: metal cage linear-gradient + border, inner slot + 4 lane spans, LED, "40G" label.
  - Layout exact: 2 rows of 24 copper ports (CSS grid repeat(24)) + 4 QSFP stacked right in ports-area.
  - Effects: multiple box-shadows for depth, gradients, transitions for hover/press, pink selection ring on .selected, title+aria for LLDP.
  - Preserved all: clickable sets selected (details/LLDP/notes), hover, activity blink from port.active, robust (missing data = down ports, loading opacity, error keeps last data).
  - Performance: 52 buttons fine (no RAF/ctx), pure CSS anims.
  - Updated index.css with .arista-chassis/.arista-inner, .rj45-port + .jack/.recess/.contacts/.port-led, .qsfp-port + .cage/.slot/.lanes, vents, labels, .ports-area etc. Flux-human tactile (not cartoon/blocky).
  - Cleanup: removed all canvas/draw/RAF/hit code, fixed labels/comments, build clean.
  - Branch: `feat/switch-react-faceplate`.
- Prior canvas work (feat/switch-realistic-physical) superseded by this React refactor per request.
  - Realistic physical Arista DCS-7050TX-48 1U viz: 48×10GBASE-T RJ45 (two rows), 4×40G QSFP+ stacked right.
## [0.3.0-alpha] - 2026-07-12

### Added
- **v0.3-alpha screenshots section** in `docs/screenshots/v0.3-alpha/` for before/after comparison:
  - New dedicated README with comparison table (Fleet/Overview, Switch section).
  - Includes the new switch faceplate (SVG physical layout), LLDP neighbors, top talkers, interface descriptions, notes.
  - References v0.2-alpha images as "Before".
  - Documents the full release of switch integration + UI refinements.
- Updated top-level `docs/screenshots/README.md` and main `README.md` to point to v0.3-alpha as current (with v0.2 archived).
- Local merge of PR branches into main (simulating GitHub PR merge for v0.3 release).
- Full documentation of all changes in `handoff.md` and this `CHANGELOG.md`.

### Changed
- Version references updated to v0.3-alpha for the switch + styling work.
- The switch visualizer is now the flagship feature of this release.

See `docs/screenshots/v0.3-alpha/README.md` for visual comparison notes. Screenshots will be populated with actual captures after testing the merged code.

### Switch Visualizer Enhancements (PRs #5, #6) - Completed
- Backend subagent (019f562a-2ff2-7e10-919c-1024e085ca18): pure eAPI-only Arista client (removed SSH/paramiko), added LLDP neighbors via "show lldp neighbors" (lldpNeighbor with system_name/port), robust interface descriptions, updated PortInfo, created dev/mock_switch.py (52-port 7050TX sim with status, desc, rates, lldpNeighbors JSON-RPC). Docs in handoff/CHANGELOG.
- Frontend subagent (019f562a-3fd9-7081-b2e8-1532a824eb19): full redesign - SVG faceplate emulating exact physical 7050TX-48T-4SFP+ (48 RJ45 + 4 SFP+ layout, clickable <g> ports with rect/LEDs, rack bezel/ears, labels), Top Talkers (rate-sorted top 5, clickable), enhanced panel (LLDP, switch desc, inline editable notes), Flux-human styling (clean .card, subtle shadows, readable, less glow, .rack-bezel/.svg-port classes). tsc/build clean. Branches updated.
- PR/docs subagent (this): ensured feat/* branches have latest (git rebase main on each; mock conflict on eapi resolved `git checkout --theirs dev/mock_switch.py` to incorporate completed 52-port LLDP mock); `git push --force origin feat/switch-eapi-lldp-mock feat/switch-svg-rack-top-talkers`; attempted GitHub API PRs via `curl -X POST .../pulls` (token ~/.hlidskjalf_gh_token) with bodies containing exact cmds + Flux refs; both 401 Bad credentials; updated handoff.md/CHANGELOG; `git commit` + push. Branches now at f0d9bd0 / 9bbfb23 (include latest main).
- PRs merged: #8, #9 via API; #6 via local after rebase (using PAT).
- Real screenshots captured: v03-fleet.png, v03-switch.png (SVG faceplate visible with LLDP, activity, notes UI), v03-node.png added to v0.3-alpha/ with updated README for before/after.
- All documented in handoff.md + CHANGELOG.

### v0.3-alpha realistic faceplate
- Switched to React + CSS for faceplate to look exactly like actual DCS-7050TX-48 photo.
- 1U physical: chassis with ears/screws/vents/bevels, exact 48 RJ45 (2 rows, jack shape, LED above), 4 QSFP (right, lanes), left mgmt ports, labels.
- React components for ports (declarative, robust, hover/click).
- CSS for realistic metal/plastic/LED blink.
- Non cartoon, human like Flux.
- PR #12.
- Screenshots updated with realistic images.

See handoff.md for subagent outputs, git commands (rebase, --theirs, force-push, curl), PR bodies, Flux inspiration.

## [0.2.0-alpha] - 2026-07-12

### Added
- **Switch port visualizer** (`/switch` page): Dedicated network section for Arista 7050TX switch.
  - Visual grid of ports showing status, speed, VLAN, activity rates.
  - Blinking cyan/pink LEDs for live network activity (based on input/output rates).
  - Editable per-port notes (stored in panel DB, merged with switch descriptions).
  - Live polling (every ~4s) for status and counters.
  - Backend support via eAPI (preferred) or SSH fallback (paramiko).
  - New config: `switch_host`, `switch_username`, `switch_password`, `switch_use_eapi`, etc.
  - Routes: `GET /api/switch/ports`, `POST /api/switch/ports/{name}/note`.
  - DB table: `switch_port_notes`.
  - Cyberpunk-themed UI matching the server room aesthetic (neon LEDs, rack labels).
- **Cyberpunk / server room theme** for screenshots documentation.
  - Restructured screenshots into versioned folders: `docs/screenshots/v0.2-alpha/`.
  - New themed READMEs with ASCII HUD, neon styling, "RACK 47" framing.
  - Updated main README "Screenshots" section to point to versioned gallery.
- **Frontend cyberpunk enhancements** (Tokyo Night + futuristic server room):
  - Added subtle grid background, neon glows on cards/buttons, hover effects.
  - Blinking/pulsing `.led` components (cyan for in, pink for out) used in network sections.
  - Improved Fleet: summary stat cards (guests, running with live LED, traffic, protected), live filter, refresh button.
  - Enhanced VM header: multi-IP chips, glowing status indicators, better layout.
  - Login page: stronger branding with neon accents.
  - Layout: improved nav with accent borders.
  - OverviewTab: integrated activity LEDs on Bandwidth and Network cards.
  - Charts and other polish for cyberpunk feel.
- New `SwitchPage.tsx` component and navigation entry.

### Changed
- **Backend normalization PR** (`feat/normalize-pve-shapes`):
  - `/api/node`: Now always provides flat `maxcpu`/`mem`/`maxmem` (extracted from `cpuinfo` or nested `memory`).
  - `/api/tasks/recent`: Normalizes so `status` is run state ("running"/"stopped") and `exitstatus` holds result.
  - Updated mock to simulate real PVE nested shape.
  - Frontend types/comments cleaned up.
  - Merged into main; handoff.md updated.
- Screenshots now versioned under `docs/screenshots/<version>/` for tracking UI evolution.
- Declared current version as **v0.2-alpha**.
- Added `paramiko` dependency for switch SSH fallback.
- Various CSS/component updates to support new blinking and cyberpunk visuals.
- Updated `hlidskjalf.env.example` with switch config section.

### Fixed
- Minor build issues during development (unused imports, etc.).
- Ensured all changes pass `pytest` (50 tests) and `npm run build`.

### Documentation
- Updated `handoff.md` with new changes and status.
- Created `CHANGELOG.md` to track all notable changes.
- Screenshots documentation now lives in its own versioned folder with immersive cyberpunk READMEs.

## [0.1.0] - Previous (PRs #1–#4 merged)

See `handoff.md` for details on PRs #1 (`feat/tests-ci`), #2 (`test/console-ws`), #3 (`deploy/docker`), #4 (`fix/ui-visual-pass`).

Initial release with full backend, frontend, tests, Docker support, etc. Tokyo Night theme established per `plan.md`.

[Unreleased]: https://github.com/jivsan/Hlidskjalf/compare/v0.2.0-alpha...HEAD
[0.2.0-alpha]: https://github.com/jivsan/Hlidskjalf/releases/tag/v0.2.0-alpha
