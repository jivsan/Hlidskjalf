# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Switch faceplate upgraded to HTML5 `<canvas>` for realistic non-cartoon 1U physical** (replaced SVG):
  - Realistic physical Arista DCS-7050TX-48 1U viz: 48×10GBASE-T RJ45 (two rows), 4×40G QSFP+ stacked right.
  - Left-side console (CON), USB, MGMT ports + status LEDs (SYS/FAN/PS1/PS2) drawn.
  - Multi-layer gradients, shadows, bevels, speculars for metal/plastic depth and 3D rack look (no blocky/cartoon).
  - Custom port drawing: RJ45 with latch notch + 8 contacts; QSFP cages with 4-lane slots.
  - Per-port LEDs above jacks with accurate blink (time-sin based on .active from portMap).
  - Selection rings (pink), hover feedback.
  - Full hit-detection via mouse coord mapping to portGeoms.
  - High-DPI (devicePixelRatio) support + RAF redraw loop.
  - Aspect-ratio wrapper + rack ears/bezel preserved for framed physical rack aesthetic.
  - Integrated with existing portMap/selected/data for status, activity, selection.
  - Updated comments + UI labels (removed all SVG refs).
  - CSS: .faceplate-wrapper + .canvas-faceplate; minor bezel padding tweak.
  - Performance: tiny static+light anim canvas, no issue.
  - Build clean; no backend changes.
- **Branch**: `feat/switch-realistic-physical` (pushed; PR #11 created via API).
- Full dev stack tested: mock_pve + backend (switch config to mock_switch https) + frontend (vite).

### Decisions: why Canvas, alternatives considered
User request: non-cartoon realistic 1U faceplate (and alternatives).
- **Chosen: Canvas** (2D HTML5 Canvas + JS draw): full programmatic control over shading, custom shapes (precise RJ45 recess/clip/pins, QSFP slots), time-based live LED activity blink (no CSS keyframes), geometry hit-test for clicks/hover, high-DPI scaling, RAF for smooth without perf DOM cost. Matches hardware exactly, easy to maintain/extend.
- **CSS (rejected)**: insufficient for non-rect complex recessed jack geometry + speculars + exact 1U spacing + dynamic per-port blink intensity based on live bps; hit areas require extra JS anyway; alignment fragile.
- **pure DOM (many divs/absolute els + bg)**: 50+ elements per faceplate = bloat, z-index/zoom/resize issues, slow for anim, poor for bevel depth without many pseudo/grad hacks.
- **image + overlays (PNG/SVG bg + pos LEDs + map areas)**: static image can't react to live rates (blinks stay cartoon), hard to sync descriptions/LEDs, imprecise hit on responsive, update burden for "physical" tweaks.
- **Three.js / WebGL / react-three-fiber (rejected)**: gross overkill for flat 2D panel (no perspective needed); adds ~100k+ bundle, GPU deps, complexity; Canvas 2D is native, zero-dep, sufficient + faster for this use case.

All documented; stack verified end-to-end before branch/PR. See handoff.md for git cmds, stack start, PR body.

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
