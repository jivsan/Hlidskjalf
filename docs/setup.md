# First-run setup

Hlidskjalf ships unconfigured. Start it, open it in a browser, and it serves a
setup wizard: point it at your Proxmox, paste an API token, create the admin
account, optionally create a first regular user. No env file required.

```bash
docker run -p 8787:8787 -v hlidskjalf-state:/var/lib/hlidskjalf ghcr.io/jivsan/hlidskjalf
# then open http://localhost:8787
```

## Before you start: make a Proxmox API token

The panel never uses root, and never uses a password. Create a scoped token:

```bash
# On the Proxmox host
pveum user add hlidskjalf@pve
pveum acl modify / --users hlidskjalf@pve --roles PVEVMAdmin,PVEDatastoreUser,PVEAuditor
pveum user token add hlidskjalf@pve panel --privsep 0
#  ^ prints the secret ONCE — copy it, the wizard needs it
```

You also want the certificate fingerprint, because the panel **pins** the Proxmox
certificate rather than trusting whatever answers on the wire:

```bash
openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
```

Paste both into the wizard. Hit **Test connection** — it makes a real API call and
tells you what it found before anything is saved.

## What the wizard writes

| Setting | Where it goes |
| --- | --- |
| Proxmox host / port / node / scheme | `config` table in the state DB |
| Proxmox token id | `config` table in the state DB |
| Proxmox token **secret** | `config` table — **encrypted** (never plaintext) |
| Proxmox cert fingerprint | `config` table in the state DB |
| Session signing secret | generated automatically if unset — also encrypted |
| Admin + first user accounts | `users` table (argon2id hashes, never plaintext) |

## Where the API key lives

You can place the Proxmox token in one of three ways. They can all be true at
once; **the environment always wins** over anything stored.

| Where | How | Good for |
| --- | --- | --- |
| **The wizard** | Type it in on first run | Getting started. It is encrypted before it touches disk. |
| **An env var** | `HLIDSKJALF_PVE_TOKEN_SECRET=...` | Declarative deploys (NixOS module, compose). Nothing is persisted. |
| **A file** | `HLIDSKJALF_PVE_TOKEN_SECRET_FILE=/run/secrets/token` | **Secret managers.** systemd `LoadCredential=`, Docker/Compose secrets and Kubernetes all hand you a *file*, not an env var. |

Prefer the `_FILE` form over the plain env var where you can: an environment
variable is visible in `/proc`, and it has a habit of turning up in logs, crash
dumps and `docker inspect`. Every secret the panel takes has a `_FILE` twin
(`HLIDSKJALF_SESSION_SECRET_FILE`, `HLIDSKJALF_SWITCH_PASSWORD_FILE`, …).

## How it is held at rest

**The token is never written to the database in plaintext.** Secrets in the state
DB are encrypted (Fernet/AES-CBC+HMAC), and the panel refuses to start rather than
run with secrets it cannot decrypt. Non-secret config (host, node, port) stays
readable, so the DB is still debuggable.

The only question is where the *encryption key* comes from, and that is what
decides how much this is actually worth:

**1. You supply the key — the mode to want.**

```bash
HLIDSKJALF_SECRET_KEY_FILE=/run/credentials/hlidskjalf.service/secret_key
# or: HLIDSKJALF_SECRET_KEY=<32-byte urlsafe-base64, or any high-entropy string>
```

Feed it from systemd `LoadCredential=`/`systemd-creds` (the key materialises in a
tmpfs only the service can read, unsealed from the TPM), a Docker/Kubernetes
secret, or a KMS. The key never rests on the panel's disk.

*Protects against:* a stolen disk image, a leaked backup, a volume snapshot,
someone walking off with the state directory. They get ciphertext.

**2. You supply nothing — the default.**

The panel generates `<state_dir>/secret.key` (`0600`) on first run, in **its own
file, separate from the database**.

*Protects against:* the realistic accident — someone copies, backs up, `scp`s, or
attaches just the `.sqlite3`. That is how these things actually leak.

*Does **not** protect against:* an attacker who can already read the state
directory as the service user or as root. They can read the key too. A key sitting
next to its own ciphertext does not stop local root, and this project is not going
to pretend otherwise. If that is in your threat model, use option 1.

**3. Keep it out of the panel's storage entirely** — set
`HLIDSKJALF_PVE_TOKEN_SECRET(_FILE)`. Env wins, so nothing is ever persisted.

Whichever you choose: the secret is never returned by any API
(`/api/debug/config` redacts it), and it is never logged.

### Rotating the token

Change it wherever you placed it — a new env var / secret file and a restart. There
is deliberately **no way to read the stored token back out** of the running panel,
so a rotation is a write, never a read-modify-write.

## Once setup is done, it is done

The setup endpoints are unauthenticated — they have to be, since nobody has
credentials yet. So they are gated on a single invariant:

> Setup is reachable **if and only if no user account exists.**

The moment the admin is created, `GET /api/setup/status` reports `needed: false`
and every setup endpoint returns `409` forever. There is no flag to flip and no
way to re-open it from the web. If there were, it would be an unauthenticated
takeover backdoor on every deployment.

To genuinely start over, stop the panel and delete the state DB.

## Skipping the wizard

Deployments that configure everything through the environment (the NixOS module,
Docker with an env file) seed an admin at startup from
`HLIDSKJALF_ADMIN_USER` + `HLIDSKJALF_ADMIN_PASSWORD_HASH`, so a user exists
immediately and the wizard never appears. Generate the hash with:

```bash
python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-password'))"
```

See `hlidskjalf.env.example` for the full list of settings.
