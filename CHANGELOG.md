# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Switch Visualizer Enhancements (PRs #5, #6) - Completed
- Backend subagent (019f562a-2ff2-7e10-919c-1024e085ca18): pure eAPI-only Arista client (removed SSH/paramiko), added LLDP neighbors via "show lldp neighbors" (lldpNeighbor with system_name/port), robust interface descriptions, updated PortInfo, created dev/mock_switch.py (52-port 7050TX sim with status, desc, rates, lldpNeighbors JSON-RPC). Docs in handoff/CHANGELOG.
- Frontend subagent (019f562a-3fd9-7081-b2e8-1532a824eb19): full redesign - SVG faceplate emulating exact physical 7050TX-48T-4SFP+ (48 RJ45 + 4 SFP+ layout, clickable <g> ports with rect/LEDs, rack bezel/ears, labels), Top Talkers (rate-sorted top 5, clickable), enhanced panel (LLDP, switch desc, inline editable notes), Flux-human styling (clean .card, subtle shadows, readable, less glow, .rack-bezel/.svg-port classes). tsc/build clean. Branches updated.
- PR/docs subagent (this): ensured feat/* branches have latest (git rebase main on each; mock conflict on eapi resolved `git checkout --theirs dev/mock_switch.py` to incorporate completed 52-port LLDP mock); `git push --force origin feat/switch-eapi-lldp-mock feat/switch-svg-rack-top-talkers`; attempted GitHub API PRs via `curl -X POST .../pulls` (token ~/.hlidskjalf_gh_token) with bodies containing exact cmds + Flux refs; both 401 Bad credentials; updated handoff.md/CHANGELOG; `git commit` + push. Branches now at f0d9bd0 / 9bbfb23 (include latest main).
- PRs (attempted): #5 https://github.com/jivsan/Hlidskjalf/pull/5 (eapi-lldp-mock), #6 https://github.com/jivsan/Hlidskjalf/pull/6 (svg-rack-top-talkers). Branches force-pushed on origin.
- All: eAPI, LLDP for machines, notes+descs, SVG physical, top talkers, rack visuals, mock, styling. Documented every step in handoff.md + this CHANGELOG.

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
