# Running Hlidskjalf with Docker

The non-Nix deployment path: a single container that builds the React SPA,
installs the FastAPI backend, and serves the panel on one port. Good for a plain
Debian VM where you don't want NixOS. (On NixOS, prefer the flake module — see
`README.md`.)

The container is self-contained except for the **manual PVE bootstrap on hella**
(the scoped API token, cloud-init template, and rescue ISO). Do that first:
`docs/bootstrap.md` §1–3. You need the token secret and the TLS fingerprint from
§1 before the panel can talk to Proxmox.

> Security: the panel is designed for LAN + a reverse proxy (Traefik/Caddy/nginx).
> Do **not** expose it directly to the internet. It talks to Proxmox with a
> non-root scoped token and pins the API cert by SHA-256 fingerprint.

## Prerequisites

- Docker Engine 24+ with the Compose v2 plugin (`docker compose`, not
  `docker-compose`). Docker 29 is known good.
- Network reachability from the VM to the Proxmox API (default `10.0.20.10:8006`).
- The PVE token secret + cert fingerprint from `docs/bootstrap.md` §1.

## Quickstart (Compose)

```bash
# 1. Get the repo onto the VM
git clone https://github.com/jivsan/Hlidskjalf.git
cd Hlidskjalf

# 2. Create your secrets file from the template
cp hlidskjalf.env.example hlidskjalf.env
chmod 600 hlidskjalf.env

# 3. Build the image once (needed for the hash helper in the next step)
docker compose build

# 4. Fill in hlidskjalf.env — see "Generating the secrets" below, then edit:
#      HLIDSKJALF_SESSION_SECRET, HLIDSKJALF_ADMIN_PASSWORD_HASH,
#      HLIDSKJALF_PVE_TOKEN_SECRET, HLIDSKJALF_PVE_FINGERPRINT, ...
$EDITOR hlidskjalf.env

# 5. Start it
docker compose up -d

# 6. Verify
curl -fsS http://127.0.0.1:8787/api/health   # -> {"ok":true}
docker compose ps                             # STATUS should show "healthy"
```

Then open `http://<vm-ip>:8787/` and log in (default user `christina`, the
password whose hash you set in step 4).

## Generating the secrets

**Session secret** (signs the session cookie):

```bash
openssl rand -hex 32
```

Paste it as `HLIDSKJALF_SESSION_SECRET`.

**Admin password hash** (argon2id — the panel stores only the hash). Using the
image you just built so you don't need Python locally:

```bash
# Simple (password visible in shell history):
docker run --rm hlidskjalf:latest \
  python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-password'))"

# No-history variant (prompts, nothing lands in history):
docker run --rm -it hlidskjalf:latest python -c '
import getpass
from argon2 import PasswordHasher
print(PasswordHasher().hash(getpass.getpass("panel password: ")))'
```

Paste the `$argon2id$...` line as `HLIDSKJALF_ADMIN_PASSWORD_HASH`, **keeping the
single quotes** the template puts around it. Docker Compose interpolates `$` in
`env_file` values, and an argon2 hash is full of `$`; the single quotes keep it
verbatim. (If you ever see login fail with a 500 right after setup, an unquoted
hash getting mangled is the usual cause.)

**PVE token secret & fingerprint**: from `docs/bootstrap.md` §1 —
`pveum user token add` prints the secret once, and

```bash
openssl s_client -connect 10.0.20.10:8006 </dev/null 2>/dev/null \
  | openssl x509 -fingerprint -sha256 -noout
```

gives the fingerprint. These go in `HLIDSKJALF_PVE_TOKEN_SECRET` and
`HLIDSKJALF_PVE_FINGERPRINT`.

## What's baked into the image vs. set at runtime

| Env var | Where set | Notes |
|---|---|---|
| `HLIDSKJALF_HOST` | image (`0.0.0.0`) | binds all interfaces *inside* the container |
| `HLIDSKJALF_PORT` | image (`8787`) | container port; remap on the host in compose |
| `HLIDSKJALF_STATIC_DIR` | image (`/app/static`) | the built SPA — don't override |
| `HLIDSKJALF_STATE_DIR` | image (`/var/lib/hlidskjalf`) | sqlite lives here; backed by the named volume |
| everything in `hlidskjalf.env` | runtime | secrets + behaviour (see the template) |

## Data & backups

State (bandwidth history, rescue boot-order stash) is a small sqlite DB in the
`hlidskjalf-state` named volume at `/var/lib/hlidskjalf`. To back it up:

```bash
docker compose cp hlidskjalf:/var/lib/hlidskjalf/hlidskjalf.sqlite3 ./hlidskjalf-backup.sqlite3
```

Bandwidth accounting only accrues while the container is running; downtime is
simply unaccounted (see `README.md` "Bandwidth accounting — known limits").

## Updating

```bash
git pull
docker compose up -d --build   # rebuilds the SPA + backend, recreates the container
```

The named volume is preserved across rebuilds, so history survives.

## Reverse proxy (recommended)

Terminate TLS at your proxy and forward to `127.0.0.1:8787`. The session cookie
is set with `secure=false` on the assumption TLS ends at the proxy on the LAN.
WebSockets (the noVNC console) must be allowed to upgrade — Traefik does this
automatically; for nginx add the usual `Upgrade`/`Connection` headers. Do not
add a public ingress.

## Without Compose (plain `docker run`)

```bash
docker build -t hlidskjalf:latest .

docker volume create hlidskjalf-state

docker run -d --name hlidskjalf \
  -p 8787:8787 \
  --env-file ./hlidskjalf.env \
  -v hlidskjalf-state:/var/lib/hlidskjalf \
  --restart unless-stopped \
  hlidskjalf:latest
```

> ⚠️ Quoting differs here. `docker run --env-file` reads values **literally** — it
> does not interpolate `$` and does not strip quotes. So on this path the argon2
> hash must be **unquoted** (remove the single quotes the template added), e.g.
> `HLIDSKJALF_ADMIN_PASSWORD_HASH=$argon2id$v=19$...`. Alternatively, leave it out
> of the file and pass it inline with shell single-quotes:
> `-e 'HLIDSKJALF_ADMIN_PASSWORD_HASH=$argon2id$v=19$...'`.

## Troubleshooting

- **`docker compose ps` shows `unhealthy`** — check `docker compose logs`. A
  bad/missing `HLIDSKJALF_SESSION_SECRET` or `HLIDSKJALF_ADMIN_PASSWORD_HASH`
  only fails at login, not at boot, so health can be green while login 500s;
  read the logs.
- **Repeated `sample skipped: PVE unreachable` warnings** — the panel booted but
  can't reach Proxmox. Check `HLIDSKJALF_PVE_HOST`/`_PORT`, routing from the VM,
  and that the token/fingerprint are correct. The panel keeps serving the UI
  regardless; only live/bandwidth data is affected.
- **`HLIDSKJALF_PVE_FINGERPRINT is required with https`** in the logs — set the
  fingerprint (bootstrap §1). The panel refuses to connect to an unverified cert.
- **Login page loads but login fails with a 500** — the password hash or session
  secret is unset/placeholder in `hlidskjalf.env`.
- **Editing `hlidskjalf.env` had no effect** — `docker compose up -d` (or
  `restart`) to recreate the container; env is read at start.
