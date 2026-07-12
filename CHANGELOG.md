# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Switch Visualizer Enhancements (feat branches + PRs coordinated)
- Pure eAPI + LLDP + mock (feat/switch-eapi-lldp)
- SVG faceplate + Top Talkers + LLDP UI + Flux-human styling (feat/switch-svg-ui; see subagent report)
- All changes, docs, branches, commits documented incrementally.
- PR descriptions prepared referencing Flux inspiration and cyberpunk but human feel.
See handoff.md for full details and branch links.

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
