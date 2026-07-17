#!/usr/bin/env python3
"""Phase 3: exercise Hlidskjalf's WRITE paths against the LIVE panel's HTTP API.

Why this exists
---------------
`scripts/validate-proxmox.py` proved the panel's READ paths against a real
Proxmox host by talking to PVE directly. This script is the other half: it
drives the PANEL's own API — the exact endpoints the SPA uses — through the
four write flows that have never run against real hardware:

    provision  ->  rescue enter/exit  ->  reinstall  ->  destroy

on ONE scratch VM it creates itself, with VMID >= 900. Every step prints
PASS / FAIL / WARN / SKIP / INFO with the observed value, in the style of
validate-proxmox.py, and on failure names the assumption that just broke.

The assumptions this is hunting (from CLAUDE.md — "still most likely wrong"):
  (a) routes/provision.py hardcodes `scsi0` for template disk reads and the
      resize call. A template whose boot disk is virtio0/sata0 silently never
      resizes — and a resize attempt against a missing scsi0 fails the whole
      provision. NOTE: the panel's API exposes NO way to read a template's
      disk layout (GET /api/vms/{vmid} omits disks), so this script provisions
      with disk_gb=1 — smaller than any real template, so the resize path is
      NEVER triggered — and warns about the blind spot instead of stepping on
      the landmine.
  (b) reinstall must preserve MAC + IP. Captured before, compared after.
  (c) rescue exit must restore the original boot order BYTE-FOR-BYTE.
      Captured before enter, compared after exit.
  (d) any task that ends with a non-OK exitstatus gets its FULL status body
      printed.

Safety
------
- Refuses --vmid < 900 outright.
- Refuses to run if the VMID (or the scratch NAME) already exists — it never
  touches a guest it did not create.
- Every destructive call goes through the panel's own confirm_name rails.
- A `finally` block always attempts cleanup: if the scratch VM still exists
  when the script ends (a mid-run failure, a Ctrl-C), it is destroyed through
  the panel and the attempt is reported. `--skip-destroy` opts out (leaves the
  VM for manual inspection) and says so loudly.
- `--dry-run` performs every read-only step (login, templates, fleet,
  provision defaults) but none of the four mutating flows — a credentials and
  reachability smoke test.
- The password, the generated cloud-init password and the CSRF token are
  never printed. Exception text is scrubbed.

Usage
-----
    HLIDSKJALF_ADMIN_PASSWORD=... python scripts/phase3-write-paths.py \
        --panel https://panel.example.org --user admin --vmid 900

    # smoke test only (no mutations):
    python scripts/phase3-write-paths.py --dry-run

Dependencies: stdlib + httpx (already a backend dependency; the repo .venv
has it). Python >= 3.12.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required: pip install httpx  (it is already a backend dependency)")


# --------------------------------------------------------------------------- #
# Result plumbing (same shape as scripts/validate-proxmox.py)
# --------------------------------------------------------------------------- #

PASS, FAIL, WARN, SKIP, INFO = "PASS", "FAIL", "WARN", "SKIP", "INFO"
_ORDER = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3, INFO: 4}


@dataclass
class Result:
    verdict: str
    check: str
    detail: str
    breaks: str = ""  # file/assumption this failure invalidates


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)

    def add(self, verdict: str, check: str, detail: str, breaks: str = "") -> Result:
        r = Result(verdict, check, self.scrub(detail), self.scrub(breaks))
        self.results.append(r)
        self._emit(r)
        return r

    def scrub(self, text: str) -> str:
        """Never let a credential reach stdout."""
        for s in self.secrets:
            if s and s in text:
                text = text.replace(s, "<REDACTED>")
        return text

    def _emit(self, r: Result) -> None:
        print(f"{r.verdict:<5} {r.check:<28} {r.detail}")
        if r.breaks:
            for line in r.breaks.splitlines():
                print(f"{'':<5} {'':<28} -> {line}")
        sys.stdout.flush()

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in _ORDER}
        for r in self.results:
            out[r.verdict] += 1
        return out


def rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 62 - len(title)))


class AbortRun(Exception):
    """A failure so fundamental that later steps are meaningless. Cleanup still runs."""


# --------------------------------------------------------------------------- #
# Panel client — the same contract the SPA uses (frontend/src/api.ts)
# --------------------------------------------------------------------------- #

CSRF_HEADER = "X-Hlidskjalf-CSRF"  # backend/hlidskjalf/auth.py:26


class Panel:
    def __init__(self, args, report: Report):
        self.a = args
        self.report = report
        self.csrf = ""
        self.node = ""
        self.role = ""
        # The mutating flows block server-side: provision/reinstall wait for the
        # PVE clone task (wait_task timeout 600s) before answering. Those calls
        # get the long timeout; everything else gets --timeout.
        self.long_timeout = max(args.timeout, 900.0)
        self.client = httpx.Client(
            base_url=args.panel.rstrip("/"),
            verify=not args.insecure,
            timeout=httpx.Timeout(args.timeout, connect=10.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        self.client.close()

    def call(self, method: str, path: str, *, long: bool = False,
             json_body: Any = None) -> tuple[int, Any, str]:
        """-> (status, data, error). Never raises; the caller decides the verdict.

        Mirrors api.ts: JSON body, cookie session (httpx's jar), and the CSRF
        header on every non-GET once we hold a token.
        """
        headers: dict[str, str] = {}
        if method != "GET" and self.csrf:
            headers[CSRF_HEADER] = self.csrf
        try:
            resp = self.client.request(
                method, path, json=json_body, headers=headers,
                timeout=self.long_timeout if long else self.a.timeout,
            )
        except Exception as e:  # TLS, DNS, refused, timeout...
            return 0, None, f"{type(e).__name__}: {e}"
        if resp.status_code >= 400:
            msg = resp.reason_phrase
            try:
                body = resp.json()
                detail = body.get("detail")
                msg = detail if isinstance(detail, str) else f"{msg}: {detail or body}"
            except Exception:
                pass
            return resp.status_code, None, msg
        try:
            return resp.status_code, resp.json(), ""
        except Exception as e:
            return resp.status_code, None, f"response was not JSON: {e}"

    def get(self, path: str) -> tuple[int, Any, str]:
        return self.call("GET", path)

    def post(self, path: str, body: Any = None, *, long: bool = False) -> tuple[int, Any, str]:
        return self.call("POST", path, json_body=body if body is not None else {}, long=long)

    def delete(self, path: str, body: Any = None, *, long: bool = False) -> tuple[int, Any, str]:
        return self.call("DELETE", path, json_body=body if body is not None else {}, long=long)

    # --- task polling: frontend/src/lib/tasks.ts::watchTask, via the panel --- #

    def poll_task(self, upid: str, rep: Report, label: str,
                  deadline_s: float = 600.0) -> bool:
        """Poll GET /api/tasks/{upid}/status until stopped. Prints the FULL body
        of any task that ends non-OK — that is assumption (d)."""
        enc = urllib.parse.quote(upid, safe="")
        deadline = time.monotonic() + deadline_s
        final: dict = {}
        while time.monotonic() < deadline:
            status, st, err = self.get(f"/api/tasks/{enc}/status")
            if err:
                rep.add(FAIL, f"{label}/task-poll",
                        f"GET /api/tasks/<upid>/status -> HTTP {status}: {err}",
                        "routes/vms.py:221 scopes task status by _vmid_from_upid. A 403 here "
                        "means the UPID failed to parse to this guest (the mock's 8-field UPID "
                        "bug, but real this time). A 404 means pve.py:task_status can't find it.")
                return False
            final = st or {}
            if final.get("status") == "stopped":
                break
            time.sleep(2.0)
        if final.get("status") != "stopped":
            rep.add(FAIL, f"{label}/task-poll",
                    f"task did not stop within {deadline_s:.0f}s (last: {final!r})")
            return False
        exitstatus = final.get("exitstatus")
        if exitstatus == "OK":
            rep.add(PASS, f"{label}/task", f"stopped, exitstatus='OK'  ({upid})")
            return True
        rep.add(FAIL, f"{label}/task",
                f"task stopped with exitstatus={exitstatus!r} — FULL status body:\n"
                f"{'':<5} {'':<28}   {json.dumps(final, indent=2, default=str)}",
                "A write task that does not exit OK is exactly what Phase 3 is hunting.\n"
                "provision.py/rescue.py treat a failed wait_task as a 500; the guest may be\n"
                "left half-cloned or half-configured. Read the body above before re-running.")
        return False

    def poll_upids(self, upids: list[str], rep: Report, label: str) -> bool:
        ok = True
        for upid in upids or []:
            ok = self.poll_task(upid, rep, label) and ok
        if not upids:
            rep.add(INFO, f"{label}/task", "the endpoint returned NO upids to poll")
        return ok


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

def step_login(panel: Panel, rep: Report) -> None:
    rule("Login + session  (main.py:/api/login, auth.py)")
    status, data, err = panel.post("/api/login",
                                   {"username": panel.a.user, "password": panel.a.password})
    if err:
        rep.add(FAIL, "auth/login", f"POST /api/login -> HTTP {status or 'no response'}: {err}",
                "Nothing below this line can be trusted. 401 => bad credentials; 403 with an\n"
                "'admin networks' message => admin login refused from this source IP\n"
                "(netzone.py) — run from inside admin_networks; 429 => login rate limit, wait\n"
                "a minute.")
        raise AbortRun
    data = data or {}
    panel.csrf = data.get("csrf") or ""
    panel.role = data.get("role") or ""
    panel.node = data.get("node") or ""
    rep.secrets.append(panel.csrf)
    if not panel.csrf:
        rep.add(FAIL, "auth/login", "200 OK but no `csrf` in the response",
                "frontend/src/api.ts::adoptSession reads res.csrf; every mutation would 403.")
        raise AbortRun
    rep.add(PASS, "auth/login",
            f"user={data.get('user')!r} role={panel.role!r} node={panel.node!r} "
            f"csrf=<{len(panel.csrf)} chars, redacted> cookie={'set' if panel.client.cookies else 'MISSING'}")
    if panel.role != "admin":
        rep.add(FAIL, "auth/role", f"logged in as role={panel.role!r}, need 'admin'",
                "provision/reinstall/destroy are admin-only (routes/provision.py). "
                "Re-run with an admin account.")
        raise AbortRun
    rep.add(PASS, "auth/role", "role is 'admin' — all four write flows are reachable")

    # The cookie round-trip: /api/session only works if the jar kept the cookie.
    status, s, err = panel.get("/api/session")
    if err:
        rep.add(FAIL, "auth/session", f"GET /api/session -> HTTP {status}: {err}",
                "The session cookie did not round-trip. Over plain http the panel only sends\n"
                "it when HLIDSKJALF_COOKIE_SECURE=false — behind https this should just work.")
        raise AbortRun
    rep.add(PASS, "auth/session", f"session round-trips (user={(s or {}).get('user')!r})")


def step_readonly_preflight(panel: Panel, rep: Report) -> tuple[list[dict], dict, str, str, str]:
    """Templates, fleet, provision defaults. Returns (templates, defaults, vlan,
    ip_cidr, gateway). Aborts when continuing is meaningless or unsafe."""
    rule("Pre-flight reads  (routes/provision.py, routes/vms.py)")

    # Templates: provision is QEMU-only, and this endpoint is the picker source.
    status, templates, err = panel.get("/api/templates")
    if err:
        rep.add(FAIL, "templates/list", f"GET /api/templates -> HTTP {status}: {err}",
                "routes/provision.py:49. Admin-only; the provision form is dead without it.")
        raise AbortRun
    templates = templates or []
    if not templates:
        rep.add(FAIL, "templates/list", "no QEMU templates visible",
                "Provisioning is impossible without one. Create a cloud-image template in\n"
                "Proxmox first. (LXC templates never show here — provision is QEMU-only.)")
        raise AbortRun
    rep.add(PASS, "templates/list",
            f"{len(templates)} QEMU template(s): "
            + ", ".join(f"{t.get('vmid')}:{t.get('name')}" for t in templates))

    # Assumption (a): the scsi0 blind spot. The panel API exposes NO template
    # disk layout — vm_detail's config block carries cores/memory/onboot/boot/
    # ostype/description only. So we cannot check the template's boot disk
    # through the panel, and we must NOT trigger the resize path (disk_gb must
    # stay below the template's real size) or a non-scsi0 template fails the
    # whole provision deep inside PVE.
    rep.add(WARN, "template/disk-blind-spot",
            "the panel API exposes NO template disk layout — cannot verify the boot disk "
            "is scsi0 through the API",
            "routes/provision.py:157-168 hardcodes scsi0 for the template size read AND the\n"
            "resize call. This script therefore provisions with disk_gb=1 (below any real\n"
            "template size) so the resize path is NEVER exercised. Before trusting provision\n"
            "with larger disks, confirm in the Proxmox UI that the template's boot disk IS\n"
            "scsi0 — if it is virtio0/sata0, size reads silently yield 0 and resize targets\n"
            "a disk that does not exist.")

    # Fleet: the VMID must be FREE. Never touch an existing guest.
    status, vms, err = panel.get("/api/vms")
    if err:
        rep.add(FAIL, "fleet/list", f"GET /api/vms -> HTTP {status}: {err}",
                "routes/vms.py:35. Cannot prove the scratch VMID is free — refusing to run.")
        raise AbortRun
    vms = vms or []
    hit = next((v for v in vms if v.get("vmid") == panel.a.vmid), None)
    if hit:
        rep.add(FAIL, "fleet/vmid-free",
                f"VMID {panel.a.vmid} ALREADY EXISTS: name={hit.get('name')!r} "
                f"status={hit.get('status')!r} kind={hit.get('kind')!r}",
                "Hard rule #1: never act on a VM this script did not create. Pick another\n"
                "--vmid (>= 900), or destroy that guest yourself if it is a leftover scratch\n"
                "from an earlier run.")
        raise AbortRun
    rep.add(PASS, "fleet/vmid-free",
            f"VMID {panel.a.vmid} does not exist ({len(vms)} guest(s) visible)")

    # Provision defaults: the source of vlans/gateways + the taken-VMID set
    # (which includes templates — GET /api/vms hides those).
    status, defaults, err = panel.get("/api/provision/defaults")
    if err:
        rep.add(FAIL, "provision/defaults", f"GET /api/provision/defaults -> HTTP {status}: {err}",
                "routes/provision.py:66. Without vlan_gateways the script cannot build a valid\n"
                "CreateVm body (vlan is validated against it).")
        raise AbortRun
    defaults = defaults or {}
    used = set(defaults.get("used_vmids") or [])
    if panel.a.vmid in used:
        rep.add(FAIL, "provision/vmid-free",
                f"VMID {panel.a.vmid} is in used_vmids (a template owns it?)",
                "Same hard rule — refusing to run against an existing guest.")
        raise AbortRun
    protected = sorted(defaults.get("protected_vmids") or [])
    if not protected:
        rep.add(WARN, "provision/protected",
                "protected_vmids is EMPTY — nothing on this host is guarded against "
                "destroy/reinstall/rescue",
                "Hard rule #2. Set HLIDSKJALF_PROTECTED_VMIDS to at least the panel's own\n"
                "host VMID before doing this for real. Continuing only because the scratch\n"
                f"VMID is {panel.a.vmid} and the script touches nothing else.")
    else:
        rep.add(PASS, "provision/protected", f"protected_vmids = {protected}")
    if panel.a.vmid in protected:
        rep.add(FAIL, "provision/protected",
                f"VMID {panel.a.vmid} is PROTECTED — destroy/reinstall/rescue would be refused",
                "Pick an unprotected scratch VMID.")
        raise AbortRun

    vlans = list(defaults.get("vlans") or [])
    gateways = dict(defaults.get("vlan_gateways") or {})
    if not vlans:
        rep.add(FAIL, "provision/vlans", "no VLANs configured (vlan_gateways is empty)",
                "_validate_create rejects any vlan not in settings.vlan_gateways. Configure\n"
                "VLANs in Settings (or HLIDSKJALF_VLAN_GATEWAYS) first.")
        raise AbortRun

    vlan = panel.a.vlan or str(vlans[0])
    if vlan not in [str(v) for v in vlans]:
        rep.add(FAIL, "provision/vlan", f"--vlan {vlan!r} not in {vlans}")
        raise AbortRun
    gateway = panel.a.gateway if panel.a.gateway is not None else str(gateways.get(vlan, ""))
    if panel.a.ip_cidr:
        ip_cidr = panel.a.ip_cidr
    elif gateway and gateway.count(".") == 3:
        # Derive a high host address in the gateway's /24. Collides with a real
        # guest only if .250 is taken — flagged in the detail line.
        base = gateway.rsplit(".", 1)[0]
        ip_cidr = f"{base}.250/24"
        rep.add(WARN, "provision/ip", f"guessed ip_cidr={ip_cidr} from gateway {gateway} "
                                      f"(override with --ip-cidr if .250 is taken)")
    else:
        ip_cidr = f"10.255.255.250/24"
        rep.add(WARN, "provision/ip",
                f"VLAN {vlan} has no gateway to derive an IP from; using placeholder "
                f"{ip_cidr} (override with --ip-cidr/--gateway)")
    rep.add(PASS, "provision/plan",
            f"vlan={vlan} ip_cidr={ip_cidr} gateway={gateway or '(none)'} "
            f"next_free_vmid={defaults.get('next_vmid')}")
    return templates, defaults, vlan, ip_cidr, gateway


def step_provision(panel: Panel, rep: Report, template_vmid: int, name: str,
                   vlan: str, ip_cidr: str, gateway: str) -> list[str]:
    rule("PROVISION  (POST /api/vms — routes/provision.py:create_vm)")
    # A cloud image sets no password of its own; without ci_password or an SSH
    # key the panel itself refuses the create (the "no way to log in" trap,
    # provision.py:194). We generate a throwaway one and never print it.
    ci_password = secrets.token_urlsafe(16)
    rep.secrets.append(ci_password)
    body = {
        "name": name,
        "template_vmid": template_vmid,
        "vmid": panel.a.vmid,          # explicit — never let the panel pick
        "cores": 1,
        "memory_mb": 1024,
        "disk_gb": 1,                  # below any real template: resize is skipped
        "vlan": vlan,
        "ip_cidr": ip_cidr,
        "gateway": gateway,
        "ssh_keys": "",
        "ci_user": "phase3",
        "ci_password": ci_password,
        "start": True,
    }
    status, data, err = panel.post("/api/vms", body, long=True)
    if err:
        rep.add(FAIL, "provision/create", f"POST /api/vms -> HTTP {status}: {err}",
                "routes/provision.py:260. This is the write path Phase 3 exists to test.\n"
                "429 => vm.provision rate limit (10/hour) — wait or restart the panel.\n"
                "A 500 mentioning scsi0/resize confirms the hardcoded-scsi0 suspicion;\n"
                "a 500 mentioning SDN.Use means the token lacks PVESDNUser (PVE 9).")
        raise AbortRun
    data = data or {}
    upids = data.get("upids") or []
    got_vmid = data.get("vmid")
    if got_vmid != panel.a.vmid:
        rep.add(FAIL, "provision/create",
                f"201 but returned vmid={got_vmid}, expected {panel.a.vmid}",
                "The panel cloned onto a VMID we did not ask for — that should be impossible.")
        raise AbortRun
    rep.add(PASS, "provision/create",
            f"201 created vmid={got_vmid}, {len(upids)} upid(s) returned"
            + (f", pangolin note={data.get('pangolin')}" if data.get("pangolin") else ""))
    # The panel already waited for the clone internally (wait_task, timeout 600)
    # before answering; these upids are the audit trail + the start task.
    if not panel.poll_upids(upids, rep, "provision"):
        raise AbortRun
    return upids


def step_wait_running(panel: Panel, rep: Report, deadline_s: float = 300.0) -> dict:
    """Poll GET /api/vms/{vmid} until status=running; then give the QEMU agent a
    short extra window to report IPs. Returns the last detail dict."""
    rule("Boot + guest-agent IPs  (GET /api/vms/{vmid} — routes/vms.py:vm_detail)")
    deadline = time.monotonic() + deadline_s
    detail: dict = {}
    while time.monotonic() < deadline:
        status, d, err = panel.get(f"/api/vms/{panel.a.vmid}")
        if err:
            rep.add(FAIL, "vm/detail", f"GET /api/vms/{panel.a.vmid} -> HTTP {status}: {err}")
            raise AbortRun
        detail = d or {}
        if detail.get("status") == "running":
            break
        time.sleep(5.0)
    if detail.get("status") != "running":
        rep.add(FAIL, "vm/running",
                f"status={detail.get('status')!r} after {deadline_s:.0f}s (last detail: "
                f"status={detail.get('status')!r} agent={detail.get('agent')})",
                "provision set start=true and the start task exited OK, yet the guest is not\n"
                "running. Look at the task log in Proxmox before re-running.")
        raise AbortRun
    rep.add(PASS, "vm/running", f"status='running' (name={detail.get('name')!r})")

    ip_deadline = time.monotonic() + 90.0
    while not detail.get("ips") and time.monotonic() < ip_deadline:
        time.sleep(5.0)
        _, detail, _ = panel.get(f"/api/vms/{panel.a.vmid}")
        detail = detail or {}
    if detail.get("ips"):
        rep.add(PASS, "vm/agent-ips",
                f"IPs visible via {'QEMU agent' if detail.get('agent') else 'ipconfig0 fallback'}: "
                f"{detail['ips']}")
    else:
        rep.add(WARN, "vm/agent-ips",
                f"no IPs after 90s of running (agent={detail.get('agent')}) — the guest-agent "
                "may not be installed in this template; ipconfig0 fallback also empty")
    rep.add(INFO, "vm/identity", f"mac={detail.get('mac')} vlan={detail.get('vlan')} "
                                 f"bridge={detail.get('bridge')} boot={detail.get('config', {}).get('boot')!r}")
    return detail


def step_console(panel: Panel, rep: Report) -> None:
    rule("Console ticket  (GET /api/vms/{vmid}/console — routes/console.py)")
    # NOTE: this endpoint is a GET, not a POST — the panel mints the PVE ticket
    # server-side and hands back a one-time local ws key. We verify the shape
    # and deliberately do NOT open the websocket.
    status, data, err = panel.get(f"/api/vms/{panel.a.vmid}/console")
    if err:
        rep.add(FAIL, "console/ticket", f"GET console -> HTTP {status}: {err}",
                "routes/console.py:77. 409 => the guest is not running; 403 => scoping bug.")
        return
    data = data or {}
    ws_path, kind, pw = data.get("ws_path"), data.get("kind"), data.get("password")
    if not ws_path or kind != "qemu":
        rep.add(FAIL, "console/ticket", f"unexpected shape: {sorted(data)}",
                "The SPA reads ws_path/kind/password from this response (console.py:113).")
        return
    rep.add(PASS, "console/ticket",
            f"ws_path={ws_path.split('?')[0]}?key=<redacted> kind={kind} "
            f"rfb-password={'present' if pw else 'MISSING'} (websocket NOT opened, by design)")


def step_rescue(panel: Panel, rep: Report, pre_detail: dict) -> None:
    rule("RESCUE enter/exit  (POST+DELETE /api/vms/{vmid}/rescue — routes/rescue.py)")
    pre_boot = (pre_detail.get("config") or {}).get("boot")
    rep.add(INFO, "rescue/boot-before", f"config.boot before enter: {pre_boot!r}")

    status, data, err = panel.post(f"/api/vms/{panel.a.vmid}/rescue", {})
    if err:
        rep.add(FAIL, "rescue/enter", f"POST rescue -> HTTP {status}: {err}",
                "routes/rescue.py:45. 500 'HLIDSKJALF_RESCUE_ISO not configured' => the\n"
                "deployment has no rescue ISO set — set it in env and re-run.\n"
                "429 => vm.rescue rate limit (10/hour).")
        raise AbortRun
    data = data or {}
    slot = data.get("slot")
    rep.add(PASS, "rescue/enter", f"rescue entered on slot={slot}, upids={len(data.get('upids') or [])}")
    if not panel.poll_upids(data.get("upids") or [], rep, "rescue-enter"):
        raise AbortRun

    # The boot order must now point AT the borrowed ide slot.
    _, detail, err2 = panel.get(f"/api/vms/{panel.a.vmid}")
    in_boot = ((detail or {}).get("config") or {}).get("boot")
    if err2:
        rep.add(FAIL, "rescue/boot-order", f"GET detail failed: {err2}")
        raise AbortRun
    want = f"order={slot}"
    if in_boot == want and (detail or {}).get("rescue"):
        rep.add(PASS, "rescue/boot-order", f"config.boot={in_boot!r} (targets the rescue ISO), "
                                           f"rescue flag=True")
    else:
        rep.add(FAIL, "rescue/boot-order",
                f"config.boot={in_boot!r} rescue={detail.get('rescue')} — expected {want!r} + rescue=True",
                "rescue.py:80 writes boot=order=<slot>. A mismatch means the guest did NOT\n"
                "boot the rescue ISO — it power-cycled straight back into its normal disk.")
        # still attempt exit so we don't strand the guest in rescue mode

    status, data, err = panel.delete(f"/api/vms/{panel.a.vmid}/rescue")
    if err:
        rep.add(FAIL, "rescue/exit", f"DELETE rescue -> HTTP {status}: {err}",
                "THE GUEST IS STILL IN RESCUE MODE. Exit it by hand (DELETE the same\n"
                "endpoint, or restore the boot order in Proxmox).")
        raise AbortRun
    rep.add(PASS, "rescue/exit", f"rescue exited, upids={len((data or {}).get('upids') or [])}")
    panel.poll_upids((data or {}).get("upids") or [], rep, "rescue-exit")

    # Assumption (c): boot order restored BYTE-FOR-BYTE.
    _, detail, err3 = panel.get(f"/api/vms/{panel.a.vmid}")
    post_boot = ((detail or {}).get("config") or {}).get("boot")
    if err3:
        rep.add(FAIL, "rescue/boot-restored", f"GET detail failed: {err3}")
        return
    if post_boot == pre_boot:
        rep.add(PASS, "rescue/boot-restored",
                f"config.boot after exit {post_boot!r} == before enter {pre_boot!r} (byte-for-byte)")
    else:
        rep.add(FAIL, "rescue/boot-restored",
                f"boot order CHANGED: before={pre_boot!r} after={post_boot!r}",
                "rescue.py:108-120 restores the stashed string via the panel's sqlite stash.\n"
                "A difference (even cosmetic, e.g. 'order=scsi0' vs 'order=scsi0;ide2') means\n"
                "PVE normalized the value on write and the stash/restore is not faithful —\n"
                "on some guests that changes which disk boots.")


def step_reinstall(panel: Panel, rep: Report, template_vmid: int, name: str) -> dict:
    rule("REINSTALL  (POST /api/vms/{vmid}/reinstall — routes/provision.py:reinstall_vm)")
    # Capture identity BEFORE: assumption (b) is that both survive.
    _, pre, err = panel.get(f"/api/vms/{panel.a.vmid}")
    if err:
        rep.add(FAIL, "reinstall/capture", f"GET detail failed: {err}")
        raise AbortRun
    pre = pre or {}
    pre_mac, pre_ips = pre.get("mac"), sorted(pre.get("ips") or [])
    rep.add(INFO, "reinstall/before", f"mac={pre_mac} ips={pre_ips}")

    status, data, err = panel.post(
        f"/api/vms/{panel.a.vmid}/reinstall",
        {"template_vmid": template_vmid, "confirm_name": name},
        long=True,
    )
    if err:
        rep.add(FAIL, "reinstall/run", f"POST reinstall -> HTTP {status}: {err}",
                "routes/provision.py:324. 400 confirm_name => the guest's name drifted from\n"
                "what we created. 429 => vm.reinstall rate limit (5/hour). A 500 deep in the\n"
                "flow may leave the guest DESTROYED-BUT-NOT-RECREATED — check the fleet.")
        raise AbortRun
    data = data or {}
    rep.add(PASS, "reinstall/run", f"reinstalled vmid={data.get('vmid')}, "
                                   f"upids={len(data.get('upids') or [])}")
    if not panel.poll_upids(data.get("upids") or [], rep, "reinstall"):
        raise AbortRun

    # The guest may still be booting; identity fields come from config, not the
    # agent, so they are readable immediately.
    _, post, err = panel.get(f"/api/vms/{panel.a.vmid}")
    if err:
        rep.add(FAIL, "reinstall/identity", f"GET detail after reinstall failed: {err}",
                "Did the guest come back at all? Check GET /api/vms.")
        raise AbortRun
    post = post or {}
    post_mac, post_ips = post.get("mac"), sorted(post.get("ips") or [])
    rep.add(INFO, "reinstall/after", f"mac={post_mac} ips={post_ips}")

    if post_mac == pre_mac and pre_mac is not None:
        rep.add(PASS, "reinstall/mac", f"MAC preserved: {post_mac}")
    else:
        rep.add(FAIL, "reinstall/mac", f"MAC CHANGED: {pre_mac} -> {post_mac}",
                "provision.py:353-360 re-applies the old MAC via _apply_cloudinit_and_size\n"
                "(mac=mac). A new MAC means DHCP reservations and any L2 pinning break on\n"
                "every reinstall — tenants notice this one immediately.")

    if pre_ips == post_ips:
        rep.add(PASS, "reinstall/ip", f"IPs preserved: {post_ips}")
    elif pre_ips and all(ip in post_ips for ip in pre_ips):
        rep.add(WARN, "reinstall/ip",
                f"configured IPs preserved but the list grew: {pre_ips} -> {post_ips} "
                "(agent vs ipconfig0 source timing, most likely)")
    elif not pre_ips and post_ips:
        rep.add(WARN, "reinstall/ip",
                f"no IPs were visible before reinstall; now {post_ips} — cannot prove "
                "preservation, likely agent timing")
    else:
        rep.add(FAIL, "reinstall/ip", f"IPs CHANGED: {pre_ips} -> {post_ips}",
                "provision.py:362-364 stashes ipconfig0 and re-applies it. Losing the static\n"
                "IP strands the guest off-network on every reinstall.")
    return post


def step_destroy(panel: Panel, rep: Report, name: str) -> bool:
    rule("DESTROY  (DELETE /api/vms/{vmid} — routes/provision.py:destroy_vm)")
    status, data, err = panel.delete(
        f"/api/vms/{panel.a.vmid}", {"confirm_name": name}, long=True)
    if err:
        rep.add(FAIL, "destroy/run", f"DELETE /api/vms/{panel.a.vmid} -> HTTP {status}: {err}",
                "routes/provision.py:405. Note: destroy passes 'destroy-unreferenced-disks'\n"
                "to PVE for BOTH qemu and lxc — real PVE may 400 on it for LXC (untested;\n"
                "this guest is qemu). 429 => vm.destroy rate limit (5/hour).")
        return False
    data = data or {}
    rep.add(PASS, "destroy/run", f"destroy accepted, upids={len(data.get('upids') or [])}"
                                 + (f", pangolin note={data.get('pangolin')}" if data.get("pangolin") else ""))
    if not panel.poll_upids(data.get("upids") or [], rep, "destroy"):
        return False

    # /cluster/resources can lag a few seconds behind a completed destroy.
    for _ in range(6):
        _, vms, err = panel.get("/api/vms")
        if err:
            rep.add(WARN, "destroy/gone", f"GET /api/vms failed while confirming: {err}")
            return False
        if not any(v.get("vmid") == panel.a.vmid for v in (vms or [])):
            rep.add(PASS, "destroy/gone", f"VMID {panel.a.vmid} no longer appears in /api/vms")
            return True
        time.sleep(5.0)
    rep.add(FAIL, "destroy/gone",
            f"VMID {panel.a.vmid} STILL LISTED 30s after the destroy task exited OK",
            "Either the PVE delete silently kept the guest (check the Proxmox UI) or\n"
            "/cluster/resources is lying to the panel. THE SCRATCH VM MAY STILL EXIST.")
    return False


def cleanup(panel: Panel, rep: Report, name: str, created: bool, destroyed: bool) -> None:
    """Finally-block cleanup: never leave the scratch guest behind by accident."""
    rule("Cleanup (always runs)")
    if not created:
        rep.add(INFO, "cleanup", "no scratch VM was created — nothing to clean up")
        return
    if destroyed:
        rep.add(PASS, "cleanup", "scratch VM already destroyed by the destroy step")
        return
    if panel.a.skip_destroy:
        rep.add(WARN, "cleanup",
                f"--skip-destroy: leaving VMID {panel.a.vmid} ('{name}') in place on purpose. "
                "Destroy it yourself when done inspecting.")
        return
    rep.add(WARN, "cleanup",
            f"scratch VM {panel.a.vmid} may still exist after a mid-run failure — "
            "attempting destroy through the panel")
    _, vms, err = panel.get("/api/vms")
    if err:
        rep.add(FAIL, "cleanup/destroy",
                f"cannot even list guests ({err}); CHECK VMID {panel.a.vmid} IN PROXMOX")
        return
    if not any(v.get("vmid") == panel.a.vmid for v in (vms or [])):
        rep.add(PASS, "cleanup", f"VMID {panel.a.vmid} is already gone (nothing to do)")
        return
    # Best-effort: exit rescue first if stranded there, then destroy. The panel
    # stops a running guest inside destroy_vm itself.
    _, detail, _ = panel.get(f"/api/vms/{panel.a.vmid}")
    if (detail or {}).get("rescue"):
        s, _, e = panel.delete(f"/api/vms/{panel.a.vmid}/rescue")
        rep.add(INFO, "cleanup/rescue-exit",
                f"stranded in rescue mode; exit attempt -> HTTP {s}{': ' + e if e else ''}")
    status, data, err = panel.delete(
        f"/api/vms/{panel.a.vmid}", {"confirm_name": name}, long=True)
    if err:
        rep.add(FAIL, "cleanup/destroy",
                f"CLEANUP DESTROY FAILED -> HTTP {status}: {err}. "
                f"VMID {panel.a.vmid} ('{name}') STILL EXISTS — destroy it yourself.")
        return
    rep.add(PASS, "cleanup/destroy",
            f"cleanup destroy accepted (upids={len((data or {}).get('upids') or [])})")
    panel.poll_upids((data or {}).get("upids") or [], rep, "cleanup")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def resolve_password(args) -> str:
    env = os.environ.get("HLIDSKJALF_ADMIN_PASSWORD", "")
    if env:
        return env
    if not sys.stdin.isatty():
        sys.exit("No password. Set HLIDSKJALF_ADMIN_PASSWORD or run interactively.")
    return getpass.getpass(f"Panel password for {args.user}@{args.panel}: ")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="phase3-write-paths.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Phase 3: drive the panel's WRITE paths (provision / rescue / reinstall /\n"
            "destroy) against the LIVE panel API, on ONE scratch VM with VMID >= 900.\n\n"
            "The read paths were validated in v0.4.0-alpha; these never have been. The\n"
            "script creates the scratch VM itself, refuses to touch anything that\n"
            "already exists, and always attempts cleanup in a finally block."
        ),
        epilog=(
            "examples:\n"
            "  HLIDSKJALF_ADMIN_PASSWORD=... python scripts/phase3-write-paths.py \\\n"
            "      --panel https://panel.example.org --vmid 900\n\n"
            "  python scripts/phase3-write-paths.py --dry-run          # credentials smoke test\n"
            "  python scripts/phase3-write-paths.py --vmid 901 --skip-destroy\n"
        ),
    )
    p.add_argument("--panel", default="https://hlidskjalf.oryxserver.org",
                   help="base URL of the panel (default: %(default)s)")
    p.add_argument("--user", default="admin", help="panel username (default: admin)")
    p.add_argument("--vmid", type=int, default=900,
                   help="scratch VMID to create (default: 900; MUST be >= 900)")
    p.add_argument("--vlan", default="", help="VLAN tag to provision onto "
                                              "(default: first from /api/provision/defaults)")
    p.add_argument("--ip-cidr", default="", help="static IP for the scratch VM, e.g. "
                                                 "192.168.20.250/24 (default: derived from the VLAN gateway)")
    p.add_argument("--gateway", default=None, help="gateway IP (default: the VLAN's configured gateway)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="per-request timeout in seconds (default: 30; mutating calls get >= 900)")
    p.add_argument("--insecure", action="store_true",
                   help="do NOT verify the panel's TLS certificate (default: verify)")
    p.add_argument("--dry-run", action="store_true",
                   help="read-only smoke test: login + templates + fleet + provision "
                        "defaults, none of the four mutating flows")
    p.add_argument("--skip-destroy", action="store_true",
                   help="leave the scratch VM in place at the end (for manual inspection)")
    return p


def run(args, rep: Report) -> int:
    panel = Panel(args, rep)
    created = destroyed = False
    name = f"phase3-scratch-{args.vmid}"
    try:
        step_login(panel, rep)
        templates, _defaults, vlan, ip_cidr, gateway = step_readonly_preflight(panel, rep)
        template_vmid = templates[0]["vmid"]
        rep.add(INFO, "templates/choice", f"using template {template_vmid} "
                                          f"('{templates[0].get('name')}') — the first QEMU template")

        if args.dry_run:
            rule("DRY RUN — stopping before any mutation")
            rep.add(SKIP, "provision/* rescue/* reinstall/* destroy/*",
                    "--dry-run: credentials, reachability and pre-flight checks passed; "
                    "no mutating call was made")
            return 0

        step_provision(panel, rep, template_vmid, name, vlan, ip_cidr, gateway)
        created = True
        detail = step_wait_running(panel, rep)
        step_console(panel, rep)
        step_rescue(panel, rep, detail)
        step_reinstall(panel, rep, template_vmid, name)
        if args.skip_destroy:
            rep.add(WARN, "destroy/skipped",
                    f"--skip-destroy: VMID {args.vmid} ('{name}') left in place")
        else:
            destroyed = step_destroy(panel, rep, name)
    except AbortRun:
        rep.add(FAIL, "run/aborted",
                "a fatal step failed — later steps would be meaningless. Cleanup follows.")
    finally:
        try:
            cleanup(panel, rep, name, created, destroyed)
        except Exception as e:  # cleanup must never mask the run
            rep.add(FAIL, "cleanup/error",
                    f"cleanup itself raised {type(e).__name__}: {e}. "
                    f"CHECK VMID {args.vmid} IN PROXMOX.")
        panel.close()

    rule("Summary")
    c = rep.counts()
    print(f"      {c[PASS]} pass   {c[FAIL]} FAIL   {c[WARN]} warn   {c[SKIP]} skip")
    if c[FAIL]:
        print("\n      Failures (each names the assumption it breaks):")
        for r in rep.results:
            if r.verdict == FAIL:
                print(f"        - {r.check}: {r.detail}")
    return 1 if c[FAIL] else 0


def main() -> int:
    args = build_parser().parse_args()
    if args.vmid < 900:
        sys.exit(f"refusing --vmid {args.vmid}: scratch VMs MUST be >= 900. "
                 "Never a real guest. (Hard rule #1.)")

    args.password = resolve_password(args)
    if not args.password:
        sys.exit("empty password")

    rep = Report(secrets=[args.password])
    print("Hlidskjalf — Phase 3 write-path validation (via the PANEL api)")
    print(f"  panel  : {args.panel}")
    print(f"  user   : {args.user} (password: {len(args.password)} chars, never printed)")
    print(f"  vmid   : {args.vmid} (scratch; created and destroyed by this script)")
    print(f"  tls    : {'INSECURE (no verification)' if args.insecure else 'verified'}")
    print(f"  mode   : {'DRY RUN (read-only)' if args.dry_run else 'FULL WRITE SEQUENCE'}"
          + (" + skip-destroy" if args.skip_destroy and not args.dry_run else ""))

    if args.insecure:
        import warnings
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    try:
        return run(args, rep)
    except KeyboardInterrupt:
        print("\nInterrupted — cleanup ran if the VM existed (see above).")
        return 130


if __name__ == "__main__":
    sys.exit(main())
