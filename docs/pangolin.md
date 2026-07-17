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
| API base URL | `HLIDSKJALF_PANGOLIN_API_URL` | e.g. `https://api.example.com/v1`, no trailing slash |
| API key (**secret**) | `HLIDSKJALF_PANGOLIN_API_KEY` | Bearer key; `*_FILE` twin supported; encrypted at rest, never returned by any API |
| Org id | `HLIDSKJALF_PANGOLIN_ORG_ID` | organization the resources live under |
| Site id | `HLIDSKJALF_PANGOLIN_SITE_ID` | numeric Newt site that can reach the VMs |
| SSH port pool base | `HLIDSKJALF_PANGOLIN_SSH_PORT_START` | e.g. `2200`; each VM gets the next free port at/above this |

On NixOS, set the non-secret four under `services.hlidskjalf.settings.pangolin*` and the
key via `environmentFile`. In the running panel, the four non-secret knobs are also
editable under **Settings** (the key is not — it is env/`_FILE` only).

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
  error. (A failed *delete* keeps the DB row so a later cleanup can still find it.)

## Security

- **SSH/TCP only.** The client hardcodes `http=false` / `protocol="tcp"`; it has no code
  path that creates a public HTTP resource. Enforced in `pangolin.py` and again at the
  provision call site, and asserted in `tests/test_pangolin.py`.
- **Key encrypted at rest** (`secretbox.py`), in `FILE_BACKED`, redacted from every API
  response — exactly like the Proxmox token.
- **Keys-only SSH.** Pair this with a keys-only VM image (no password auth) so an
  internet-reachable SSH port cannot be password-brute-forced.
