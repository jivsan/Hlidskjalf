# Hlidskjalf v0.3.5-alpha — screenshot gallery

A **design-system pass**. The panel now has a deliberate identity built around its
subject: Hlidskjalf, the high seat from which one watches every guest running on
the host "hella". Captured live against the dev stack (mock PVE + mock switch +
backend serving the built frontend, `HLIDSKJALF_DEBUG=true`).

## What changed vs [v0.3.4-alpha](../v0.3.4-alpha/)

- **Typography is the concept.** Archivo (variable, wide + heavy) now carries the
  human interface — wordmark, headings, nav, labels — while JetBrains Mono is
  reserved strictly for machine data (metrics, IDs, UPIDs, MACs, IPs, bytes, the
  faceplate). The split *means* something instead of being mono-everywhere.
- **Signature masthead + login.** The wordmark is set monumental (Archivo 800 /
  width 125%); the sidebar is a high-seat identity rail with a live "hella" pulse
  and an aurora-bar active nav. Login is a quiet hero with a one-line thesis and a
  single orchestrated page-load reveal. Cohesive verbs: *take the seat* / *leave
  the seat*.
- **Every page opens on one rhythm** — an eyebrow (labeled hairline) + a display
  title via the shared `<PageHeader>`. Cards gained real elevation; recessed
  `.well` panels hold data dumps (Debug, config).
- **Disciplined palette.** Deeper night, two surface levels + an abyss, brighter
  text. Accent hues unchanged but disciplined: cyan = live, pink = brand +
  selection only, amber = attention, red = danger. No more pink everywhere.

## Admin panel

| View | File |
| --- | --- |
| Login (hero) | `login.png` |
| Fleet | `admin-fleet.png` |
| VM detail | `admin-vm-detail.png` |
| Provision | `admin-provision.png` |
| Node | `admin-node.png` |
| Switch (faceplate) | `admin-switch.png` |
| Users | `admin-users.png` |
| Debug | `admin-debug.png` |

## User (VPS customer) panel

| View | File |
| --- | --- |
| My VM | `user-my-vm.png` |
| Switch | `user-switch.png` |

Re-capture with `capture.js` (needs `puppeteer-core` + system Chromium; run the
dev stack first — see `handoff.md` for the exact mock_switch TLS + fingerprint
setup). Writes into this folder by default.
