# Hlidskjalf v0.3.6-alpha — screenshot gallery

The release that makes the panel **runnable by other people**: it ships
unconfigured and sets itself up in a browser. Captured live against the dev stack
(mock PVE + mock switch + backend serving the built frontend).

## First-run setup wizard (new)

An unconfigured panel serves this instead of the login page. Nothing is persisted
until the connection is proven, and once an admin exists these endpoints close
**forever** (they're unauthenticated by necessity — see `docs/setup.md`).

| Step | File | Notes |
| --- | --- | --- |
| 1 · Proxmox connection | `setup-1-connect.png` | host/node/token + optional cert fingerprint. The `pveum` command to mint the token is inline. |
| 1 · connection verified | `setup-2-tested.png` | **Test connection** makes a real API call — "Proxmox answered. node hella · 9 guests". Continue stays disabled until it succeeds, and editing any field invalidates the result. |
| 2 · Admin account | `setup-3-admin.png` | username + password + confirm (min 8). |
| 3 · First user | `setup-4-first-user.png` | Optional, **skipped by default**. The VM is chosen from a **picker of the guests the connection test actually found** — not a VMID typed from memory. |
| 4 · Review | `setup-5-review.png` | The token secret renders as `•••••••• (held, not shown)` and is never echoed back. "finish & take the seat" signs you straight in. |

## Admin panel

| View | File |
| --- | --- |
| Login | `login.png` |
| Fleet | `admin-fleet.png` |
| VM detail | `admin-vm-detail.png` |
| Provision | `admin-provision.png` |
| Node | `admin-node.png` |
| Switch | `admin-switch.png` |
| Users | `admin-users.png` |
| Debug | `admin-debug.png` |

## User (VPS customer) panel

| View | File |
| --- | --- |
| My VM | `user-my-vm.png` |
| Switch | `user-switch.png` |

## What changed visually vs [v0.3.5-alpha](../v0.3.5-alpha/)

The design system is unchanged — v0.3.6 is a security/product release. The one
visible difference is that **the panel no longer hardcodes a host name**: the node
comes from `/api/session`, so the sidebar and the Fleet eyebrow now read "hella"
because that's what *this* deployment watches, not because it's baked into the
source. The login screen deliberately names no host at all (it renders pre-auth).

## Re-capturing

Both scripts need `puppeteer-core` + system Chromium and write into this folder.

- `capture.js` — the configured panel on `:8787` (see the dev-stack cheat sheet in
  `handoff.md`; copy `dev/dev.env.example` → `dev/dev.env` first).
- `capture-setup.js` — the wizard. Needs a genuinely **unconfigured** backend:
  ```bash
  HLIDSKJALF_STATE_DIR=/tmp/hlidskjalf-setup-demo \
  HLIDSKJALF_PVE_HOST="" HLIDSKJALF_COOKIE_SECURE=false \
  HLIDSKJALF_STATIC_DIR=$PWD/frontend/dist \
  uvicorn hlidskjalf.main:app --port 8790     # with ADMIN_PASSWORD_HASH unset
  ```
