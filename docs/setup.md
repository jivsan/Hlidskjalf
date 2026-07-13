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
| Proxmox token id + **secret** | `config` table in the state DB |
| Proxmox cert fingerprint | `config` table in the state DB |
| Session signing secret | generated automatically if unset |
| Admin + first user accounts | `users` table (argon2id hashes, never plaintext) |

## Where secrets live, honestly

The token secret is stored in the SQLite state DB (`hlidskjalf.sqlite3`). The DB
file is created `0600` and its directory `0700`, so it is readable only by the
user the panel runs as — the same protection a root-only `EnvironmentFile` gets.
The secret is never returned by any API (`/api/debug/config` redacts it), and it
is never logged.

It is **not encrypted at rest**, and deliberately so: an encryption key stored on
the same disk, readable by the same user, protects against nothing. Anyone who can
read the DB file can read the key. Encrypting it would look reassuring without
being reassuring, and this project would rather tell you the truth.

**If you want the secret out of the database entirely, put it in the environment.**
Environment variables always take precedence over anything the wizard stored:

```bash
HLIDSKJALF_PVE_TOKEN_SECRET=...   # from agenix / sops-nix / systemd-creds / Docker secret
```

Set that, and the panel uses it and ignores the stored copy. This is the
recommended path for the NixOS module (`nix/module.nix` already takes an
`EnvironmentFile`), and it means your secret lives wherever you already manage
secrets. The wizard exists so that people *without* that setup can get running in
two minutes — not to replace it.

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
