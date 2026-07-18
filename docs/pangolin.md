# Pangolin SSH-tunnel integration (optional)

When configured, the panel auto-creates a [Pangolin](https://docs.pangolin.net) **TCP
resource** that tunnels SSH (port 22) to every VM it provisions, and deletes that
resource when the VM is destroyed. A tenant then reaches their VM from anywhere with:

```
ssh -p <proxy_port> user@<your-pangolin-domain>
```

No SSH port is opened on your WAN — the connection rides the same Pangolin tunnel as
the panel. The VM stays keys-only (the SSH key is the auth), and the panel **never**
creates a public HTTP resource — only SSH/TCP.

The integration is **off by default**. A fresh clone, and any deployment that does not
configure it, is entirely unaffected.

## What you need

1. A Pangolin install with a **Newt site** that can reach your VMs' network (e.g. the
   host that runs `newt` and can route to the VM subnet).
2. An **Integration API key**, org-scoped (Pangolin → Integration API → create key).
   This key can create and delete resources — treat it like the Proxmox token.
3. The **org id** and the numeric **site id** (visible in the Pangolin UI / API:
   `GET /org/{orgId}/site`).

## Configuration

All five must be set for the integration to turn on (`Settings.pangolin_enabled`):

| Setting | Env var | Notes |
|---|---|---|
| API base URL | `HLIDSKJALF_PANGOLIN_API_URL` | e.g. `https://api.example.com/v1`, no trailing slash. **Must be `https://`** — the API key rides there as a bearer token, so the panel refuses to start with a plain-`http` URL (http is accepted only for loopback hosts, i.e. a local mock) |
| API key (**secret**) | `HLIDSKJALF_PANGOLIN_API_KEY` | Bearer key; `*_FILE` twin supported; encrypted at rest, never returned by any API |
| Org id | `HLIDSKJALF_PANGOLIN_ORG_ID` | organization the resources live under |
| Site id | `HLIDSKJALF_PANGOLIN_SITE_ID` | numeric Newt site that can reach the VMs |
| SSH port pool base | `HLIDSKJALF_PANGOLIN_SSH_PORT_START` | e.g. `2200`; each VM gets the next free port at/above this |

All five are **environment-only** (or the NixOS module: the non-secret four under
`services.hlidskjalf.settings.pangolin*`, the key via `environmentFile`). They are
deliberately NOT editable in the running panel's Settings page — the API URL is
TLS-validated at startup, and a value written through the database would bypass
that check.

## Behaviour

- **On provision** (`POST /api/vms`): a TCP resource named after the VM is created, a
  target is attached pointing at the VM's static IP `:22` via your site, and the
  `(vmid, resource_id, proxy_port)` mapping is stored. The chosen port comes back in the
  create response under `pangolin.ssh_port`.
- **On destroy**: the resource is deleted and the mapping removed.
- **Reinstall** keeps the same VMID/IP, so the existing resource keeps working — nothing
  is recreated.
- **Best-effort**: if Pangolin is unreachable or errors, the VM create/destroy still
  succeeds. The failure is logged and returned as a `pangolin.warning` note, never an
  error. A failed *delete* keeps the resource id on the VM's DB row as an orphan debt —
  across reprovisions too — and the next destroy of that VMID retries every owed delete.
- **Out-of-band deletes**: if the VM was destroyed outside the panel (e.g. the Proxmox
  UI), the panel's destroy still runs the Pangolin cleanup and then reports the VM as
  already gone, instead of 404ing and stranding the tunnel.

## Security

- **SSH/TCP only.** The client hardcodes `http=false` / `protocol="tcp"`; it has no code
  path that creates a public HTTP resource. Enforced in `pangolin.py` and again at the
  provision call site, and asserted in `tests/test_pangolin.py`.
- **Key encrypted at rest** (`secretbox.py`), in `FILE_BACKED`, redacted from every API
  response — exactly like the Proxmox token.
- **Keys-only SSH.** Pair this with a keys-only VM image (no password auth) so an
  internet-reachable SSH port cannot be password-brute-forced.

---

# Tenant identity sync — SSO at the edge (optional)

The SSH integration above tunnels *VM access*. This second, separate integration covers
*panel access*: when the panel itself is published through a Pangolin resource
(`docs/public-access.md`), that resource should keep **Platform SSO ON** — it is the
phishing-resistant edge (per-user revocation, edge audit, and **passkey** support).
For tenants to pass the wall, they need a Pangolin identity — and this sync provisions
it from the place you already manage tenants: the panel's Users page.

**On create** (user with an email): the panel invites that email into your Pangolin
org's tenant role with `sendEmail: false`, and shows **you** the invite link exactly
once — you relay it out-of-band (Signal, in person). The friend sets their own
Pangolin password; the panel never sees it. Panel password and Pangolin password are
independent credentials on two independent walls.

**On delete**: the panel removes the edge identity too — the org user is deleted
(matched by the invited *email*: the invitee picks their username at accept time, and
an account under a different email is never touched), or the unaccepted invite is
cancelled. Offboarding is complete in one click.

**Retry/refresh**: `POST /api/users/{name}/pangolin-sync` (the retry chip in Users)
re-invites after a failure, and flips `invited → active` once the friend accepts.

All of it is **best-effort**: a Pangolin outage never fails a panel user create or
delete — the failure lands in the audit log and on a red `error` chip with a retry
button. The sync is **off by default**.

## Setup (one-time, four steps)

1. **Pangolin dashboard → the panel's resource → Authentication**: keep Platform SSO
   **on**, and add the tenant role (default `Member`) to **Roles**. Every invitee joins
   that role, so they pass by construction — no per-user resource lists to maintain.
2. **Integration API key**: add the actions `inviteUser`, `listRoles`, `getOrgUser`,
   `removeUser`. (Still no resource-creation rights needed for this part.)
3. **Panel env**:

   | Setting | Env var | Notes |
   |---|---|---|
   | Sync on/off | `HLIDSKJALF_PANGOLIN_SYNC_USERS` | `true` to enable (default `false`) |
   | Tenant role | `HLIDSKJALF_PANGOLIN_TENANT_ROLE` | org role to invite into (default `Member`) |

   (plus the five connection knobs from the table above — the sync rides the same
   Integration API connection).
4. **Each tenant**: invite link → set password → then **add a passkey** in Pangolin.
   That edge login is the phishing-proof one.

## Security

- **Invite links are bearer secrets.** Returned by the API once, shown to the admin
  once, never stored in the panel's database, never logged, and they expire
  (`validHours`, 72 h by default).
- **Least privilege**: the sync uses only invite/lookup/remove-user actions — it
  cannot create, modify or delete any Pangolin *resource*.
- **The email is the key.** Lookups match the invited email exactly (substring hits
  like `malice@example.com` are rejected client-side), so a pre-existing Pangolin
  account that merely shares a panel username is never deleted.

