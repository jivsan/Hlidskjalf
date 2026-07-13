# Hlidskjalf v0.3.4-alpha — screenshot gallery

Captured live against the dev stack (mock PVE + mock switch + backend serving
the built frontend, `HLIDSKJALF_DEBUG=true`). This release is a **frontend-only
robustness + design pass** — see `CHANGELOG.md` for the full list.

Visible changes vs [v0.3.2-alpha](../v0.3.2-alpha/):

- **Login** — redesigned: gradient accent card, radial backdrop glow, larger wordmark.
- **Sidebar** — accent-bar active nav, role badge (pink admin / cyan user), tagline.
- **Users** — full rewrite: role select, VM assign / password reset / delete via
  proper modals (no more `prompt()` dialogs), free-VM filtering, validation.
- **Switch faceplate** — ports now span the full chassis width in 2×24 rows
  (a duplicated CSS block had been squashing them into the corner).
- **User view** — Danger zone (reinstall/destroy) no longer shown to customers;
  those actions are admin-only server-side.
- **Debug** — objects render as JSON instead of `[object Object]`.

## Admin panel

| View | File |
| --- | --- |
| Login | `login.png` |
| Fleet | `admin-fleet.png` |
| VM detail (overview) | `admin-vm-detail.png` |
| Provision | `admin-provision.png` |
| Node | `admin-node.png` |
| Switch (DCS-7050TX-48 faceplate) | `admin-switch.png` |
| Users management | `admin-users.png` |
| Debug (HLIDSKJALF_DEBUG=true) | `admin-debug.png` |

## User (VPS customer) panel

| View | File |
| --- | --- |
| My VM (scoped, no danger zone) | `user-my-vm.png` |
| Switch activity | `user-switch.png` |

Re-capture with `capture.js` (needs `puppeteer-core` + system Chromium; run the
dev stack first — see the cheat-sheet in `handoff.md`, and start `mock_switch`
with its TLS cert: `uvicorn mock_switch:app --port 18080 --ssl-certfile
mock_switch.crt --ssl-keyfile mock_switch.key`, backend with
`HLIDSKJALF_SWITCH_FINGERPRINT=<sha256 of mock_switch.crt>`).
