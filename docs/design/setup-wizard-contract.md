# First-run setup wizard — API contract (v0.3.6)

The panel ships unconfigured. On first start (no users exist), it serves a setup
wizard instead of the login page. The wizard collects the Proxmox connection, the
admin account, and optionally a first regular user — then logs the admin straight in.

## Why "no users exist" is the gate
`setup_needed` is true **iff the users table is empty**. Env-configured deployments
(NixOS/Docker with `HLIDSKJALF_ADMIN_PASSWORD_HASH`) seed an admin at startup, so
they never see the wizard. Once *any* user exists, every setup endpoint refuses
forever — otherwise it would be a permanent takeover backdoor.

## Endpoints (all unauthenticated, all dead once setup completes)

### `GET /api/setup/status`
Always available. Reveals nothing but whether setup is required.
```json
{ "needed": true }
```

### `POST /api/setup/test`
Dry-run: validate a Proxmox connection without persisting anything. Lets the wizard
say "that token works" before committing.

Request:
```json
{ "host": "192.168.1.10", "port": 8006, "node": "pve", "scheme": "https",
  "token_id": "hlidskjalf@pve!panel", "token_secret": "xxxxxxxx-...",
  "fingerprint": "", "verify_tls": true }
```
Responses:
- `200` — `{ "ok": true, "node": "pve", "guests": 7, "nodes": ["pve"] }`
- `400` — `{ "detail": "Could not reach Proxmox at …: connection refused" }` (or bad token / unknown node / TLS pin mismatch)
- `409` — `{ "detail": "Setup has already been completed" }`

### `POST /api/setup`
Commits the configuration. Validates the PVE connection first — **nothing is
persisted if it fails**. Creates the admin (and optional first user), then signs
the admin in (sets the session cookie) so the wizard lands straight in the panel.

Request:
```json
{
  "pve": {
    "host": "192.168.1.10", "port": 8006, "node": "pve", "scheme": "https",
    "token_id": "hlidskjalf@pve!panel", "token_secret": "xxxxxxxx-...",
    "fingerprint": "", "verify_tls": true
  },
  "admin": { "username": "admin", "password": "at-least-8-chars" },
  "user":  { "username": "customer", "password": "at-least-8-chars", "vmid": 105 }
}
```
`user` is **optional** — send `null` or omit it. `fingerprint` is optional (pins the
PVE cert by SHA-256); `verify_tls: false` disables verification (dev only).

Responses:
- `200` — same shape as `POST /api/login`, and sets the session cookie:
  ```json
  { "ok": true, "csrf": "…", "user": "admin", "role": "admin", "vmid": null, "node": "pve" }
  ```
- `400` — validation or PVE-connection failure, `{ "detail": "…" }`
- `409` — `{ "detail": "Setup has already been completed" }`
- `429` — rate limited (same per-IP limiter as login)

## Rules for the frontend
1. Call `GET /api/setup/status` **before** deciding what to render. If `needed`,
   the ONLY reachable route is the wizard — no login, no panel.
2. On a successful `POST /api/setup`, the user is already authenticated: store the
   returned session exactly as `login()` does and go to `/`.
3. Passwords: minimum **8** characters (the backend enforces this too).
4. Never display or log the token secret after submission.
5. `POST /api/setup/test` should be wired to a "Test connection" button and its
   result shown inline before the user can finish.

## Where secrets go
The wizard writes config (including the PVE token secret) into a `config` table in
the SQLite state DB, which is created `0600`. **Environment variables always win
over stored config** — so an operator who prefers agenix/sops/systemd-creds can keep
secrets out of the DB entirely and the wizard simply never overrides them.
