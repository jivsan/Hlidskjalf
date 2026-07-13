# Real-hardware validation

## Why this exists

**Hlidskjalf has never been run against a real Proxmox host.**

The backend has 163 tests and they are all green. Every one of them runs against
`dev/mock_pve.py` — a mock **we wrote ourselves**, from our own reading of the Proxmox
API. A green suite proves the panel is *self-consistent with our own assumptions*. It
does not prove the panel works. If an assumption is wrong, the mock is wrong in exactly
the same way, the tests pass, and we learn nothing.

So: nobody knows if this thing actually works. This document and
`scripts/validate-proxmox.py` exist to find out, in about an hour, without breaking
anything.

The script is not a test suite. It is a list of *the specific things we guessed at*,
checked one at a time against reality, each printing the value actually observed and —
when it fails — the file and the line whose assumption just died.

### It already caught one

Run the script against our own mock and it fails on `upid/vmid-field`:

```
INFO  upid/example      UPID:hella:00000001:1783938274:qmstart:140:mock@pve:   (8 fields)
FAIL  upid/vmid-field   _vmid_from_upid(...) = None, but the task's id is 140.
```

`dev/mock_pve.py::_mk_upid` omits the `pstart` field, so it emits **8**-field UPIDs
where real Proxmox emits **9**. `routes/vms.py::_vmid_from_upid` reads `parts[6]` — on
a mock UPID that is `mock@pve`, which parses to `None`, which the panel treats as a
*node-level task* and restricts to admins. Meaning: **against the mock, a regular user
polling their own power-action task gets a 403 — and no test noticed**, because the
security tests (`test_security_v036.py`) hand-write *correct* 9-field UPIDs instead of
using the ones the mock actually hands out.

The panel's parser looks right for real Proxmox. The mock is what's lying. But that is
precisely the point: this is what an untested assumption looks like from the inside,
and this one sits on an authorization check.

---

## 1. Create a scoped API token (safely)

**Never `root@pam`. Never a password.** On the Proxmox host:

```bash
pveum user add hlidskjalf@pve
pveum acl modify /vms       --users hlidskjalf@pve --roles PVEVMAdmin       # guests
pveum acl modify /storage   --users hlidskjalf@pve --roles PVEDatastoreUser # clone disks
pveum acl modify /          --users hlidskjalf@pve --roles PVEAuditor       # GET /nodes, tasks
pveum acl modify /sdn/zones --users hlidskjalf@pve --roles PVESDNUser       # NIC -> bridge/VLAN (PVE 9)
pveum user token add hlidskjalf@pve panel --privsep 0    # prints the secret ONCE
```

Two things bite people here:

- **`--privsep 0` matters.** With privilege separation *on*, the token gets only the
  privileges granted to the **token itself**, not to the user. It will authenticate
  perfectly and then 403 on everything. The script's `auth/permissions` check exists
  purely to catch this.
- **`PVEAuditor` alone is not enough.** It is read-only: no `VM.Console` (no noVNC), no
  `VM.PowerMgmt` (no start/stop), no `VM.Allocate`/`VM.Clone` (no provisioning). The
  script's `auth/privileges` check lists exactly which privileges are missing and which
  panel feature each one breaks. If you want a genuinely read-only first pass, use
  `PVEAuditor` alone and expect those checks to WARN.

The token id is the **full** `user@realm!tokenname` — `hlidskjalf@pve!panel`.

### Get the TLS fingerprint

`backend/hlidskjalf/pve.py` pins the Proxmox certificate by SHA-256 and **refuses to
connect over https without one**. On the host:

```bash
openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
```

(If you forget, run the script without `--fingerprint`: it fails the pin check and
prints the fingerprint the host actually presented, ready to paste.)

---

## 2. Run the script

```bash
python scripts/validate-proxmox.py \
    --host 192.168.1.10 \
    --node pve \
    --token-id 'hlidskjalf@pve!panel' \
    --fingerprint AA:BB:CC:...:FF
```

It prompts for the token secret. You can also use `--token-secret-file /path` or the
`HLIDSKJALF_PVE_TOKEN_SECRET` env var. Avoid `--token-secret` — it lands in your shell
history. **The secret is never printed**, and any exception text that contains it is
scrubbed before it reaches the terminal.

### What it will and will not do to your Proxmox

- **Read-only by default.** No guest is created, destroyed, powered, resized or
  reconfigured.
- The **one** non-GET in the default path is `POST .../vncproxy`, which mints a
  short-lived VNC ticket. It spawns a proxy process on the host and changes **no guest
  state**. Pass `--no-console` to skip even that.
- Mutating anything requires **both** `--allow-writes` **and** `--vmid <scratch vmid>`,
  and the script **refuses any vmid below 900**. Even then, all it does is `start` a
  *stopped* scratch guest, validate the UPID Proxmox hands back, and `stop` it again.
  If the scratch guest is already running it refuses to touch it.

Exit code is `0` if nothing failed, `1` if any check FAILed, `2` if it could not
authenticate.

### Reading the output

```
PASS  nodes/single-node       single node 'pve' — matches the panel's model
WARN  resources/qemu-disk     8/8 running QEMU guests report disk=0
FAIL  console/websocket       handshake FAILED: InvalidStatus: HTTP 401
                              -> routes/console.py:122-128 dials vncwebsocket with ONLY
                              -> the PVEAPIToken header. If Proxmox will not authenticate
                              -> the websocket upgrade with an API token ...
```

- **PASS** — the assumption holds. Move on.
- **WARN** — works, but not the way we assumed, or a feature will look wrong.
- **FAIL** — an assumption in the code is broken. The indented `->` lines name the file
  and explain what breaks and why.
- **INFO** — the observed value, recorded so you can eyeball it.

When something FAILs: **fix the code, add a test that would have caught it, and fix
`dev/mock_pve.py` too.** If you fix the panel but not the mock, the suite goes green
against the wrong thing again and we are back where we started.

### Running it against the mock proves nothing

```bash
cd dev && python -m uvicorn mock_pve:app --port 18006 &
python scripts/validate-proxmox.py --scheme http --host 127.0.0.1 --port 18006 \
    --node hella --token-secret mock-secret
```

This is useful for exactly one thing: confirming **the script itself** runs. A clean
run here says nothing whatsoever about real Proxmox — it is the mock agreeing with the
mock. (Two results are expected and correct against the mock: `upid/vmid-field` FAILs,
as described above, and `console/rfb-handshake` WARNs because the mock's websocket is a
byte echo with no VNC server behind it.)

---

## 3. Manual checklist — the things a script cannot check

A script can prove the API shapes are right. It cannot prove the panel *feels* right, and
it cannot prove the console works. Do these by hand, in order.

### Before you start — the two rules

1. **Put the panel's own host VM in `HLIDSKJALF_PROTECTED_VMIDS` before the first
   start.** The default is **empty** — meaning *nothing is protected* and an admin can
   destroy the VM the panel is running on, from inside the panel.
   ```bash
   HLIDSKJALF_PROTECTED_VMIDS=<panel-host-vmid>,<anything-else-precious>
   ```
2. **Do every destructive test on a scratch VM with VMID >= 900.** Never on a real one.
   Clone a template to `901`, name it `scratch-validate`, and confine yourself to it.

### The checklist

- [ ] **Setup wizard.** Start the panel unconfigured. Does it find the node, list the
      real guests, and accept the fingerprint? (This is `GET /nodes` with a scoped
      token — if it fails, first-run is broken for everyone.)
- [ ] **Fleet list.** Do all guests appear, with plausible CPU/RAM/uptime? Note whether
      QEMU disk usage reads 0% — expected on real Proxmox, and the mock hides it.
- [ ] **The console. Open it on the scratch VM and actually type in it.**
      This is **the single biggest unknown in the whole panel.** The noVNC byte-pump in
      `routes/console.py` has never moved a byte of real RFB traffic. The mock has no VNC
      server — it is a `send_bytes(await receive_bytes())` echo. Specifically check:
      - the canvas paints (not a blank/grey screen),
      - **keystrokes reach the guest**,
      - the mouse tracks,
      - it survives a minute idle without the socket dropping.
      If the handshake works but the screen stays blank, suspect the subprotocol or the
      ticket; if it 401s, Proxmox is refusing to authenticate a websocket upgrade with an
      API token and the console needs redesigning around `/access/ticket`.
- [ ] **Power-cycle the scratch VM from the UI.** Start, shutdown, reboot. Does the task
      progress widget actually track to completion? (That widget polls
      `/api/tasks/<upid>/status` — the UPID parsing path.)
- [ ] **Power-cycle it as a *regular user*, not an admin.** Create a user scoped to VMID
      901, log in as them, start the VM, and watch the task poll. **This is the path the
      UPID bug lives on.** An admin will never see it — `_vmid_from_upid` returning
      `None` falls through to "admins only" and an admin sails past.
- [ ] **Guest agent absent.** Point the panel at a VM with **no** qemu-guest-agent
      installed. The detail page must degrade (fall back to `ipconfig0`, show no IPs) —
      **not 500**.
- [ ] **Protected VMID actually refuses destroy.** Put a VMID in
      `HLIDSKJALF_PROTECTED_VMIDS`, then try to destroy it from the UI. It must be
      refused **server-side** (not merely hidden in the frontend). Confirm the same for
      `stop`, `reset` and `reinstall`, and that `shutdown`/`reboot` remain allowed.
- [ ] **Destroy confirmation.** Destroying requires typing the exact VM name. Try a
      wrong name; it must refuse.
- [ ] **Bandwidth accumulator books data.** Leave the panel running for **at least an
      hour** with a guest doing real traffic, then check the bandwidth page shows
      non-zero bytes for today. The accumulator samples every 60 s (`accumulator.py`);
      one sample establishes only a baseline and counts nothing, so a short run looks
      identical to a broken one.
- [ ] **Counter reset.** Reboot the scratch VM and confirm the bandwidth total does not
      jump wildly or go backwards (`accumulator.py`'s counter-reset rule).
- [ ] **Provision a VM from a template** (on a scratch VMID). Does the clone complete,
      does cloud-init apply, does the VLAN tag land with `firewall=0`, does it boot with
      the static IP?
- [ ] **Reinstall the scratch VM.** Does it preserve MAC, VLAN and IP?
- [ ] **Then destroy the scratch VM** and confirm it is gone from Proxmox, not just from
      the panel.

### Report what you find

Anything that failed: fix it, add a regression test, **and fix `dev/mock_pve.py` so the
mock stops lying**. Update `handoff.md`. Otherwise the next session reads 163 green
tests and believes them.
