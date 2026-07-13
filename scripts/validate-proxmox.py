#!/usr/bin/env python3
"""Validate Hlidskjalf's assumptions against a REAL Proxmox VE host.

Why this exists
---------------
Every one of Hlidskjalf's tests runs against `dev/mock_pve.py` — a mock the
maintainers wrote themselves. A green suite proves the panel is *self-consistent
with our own guesses*, not that it works. The panel has never talked to a real
Proxmox host. This script is the first contact with reality.

It checks, one by one, the assumptions the panel is actually built on, and prints
PASS / FAIL / WARN with the observed value and — on failure — which file and which
assumption just broke.

Safety
------
- READ-ONLY by default. No guest is created, destroyed, powered, resized or
  reconfigured unless you pass BOTH `--allow-writes` AND `--vmid <scratch vmid>`,
  and the scratch vmid must be >= 900.
- The one exception to "no POST" in the default path is `POST .../vncproxy`, which
  mints a short-lived VNC ticket. It spawns a proxy process on the PVE host and
  changes NO guest state. Pass `--no-console` to skip it entirely.
- The token secret and the VNC ticket are never printed. Exception text is scrubbed.

Usage
-----
    python scripts/validate-proxmox.py \\
        --host 192.168.1.10 --node pve \\
        --token-id 'hlidskjalf@pve!panel' \\
        --fingerprint AA:BB:...:FF

The secret is read (in order) from `--token-secret-file`, the env var
`HLIDSKJALF_PVE_TOKEN_SECRET`, or an interactive prompt. `--token-secret` exists for
scripting but puts the secret in your shell history — prefer the file or the prompt.

Dependencies: stdlib + httpx + websockets (both already backend dependencies).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import os
import socket
import ssl
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required: pip install httpx  (it is already a backend dependency)")

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # console check degrades to SKIP


# --------------------------------------------------------------------------- #
# Result plumbing
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
    secret: str = ""

    def add(self, verdict: str, check: str, detail: str, breaks: str = "") -> Result:
        r = Result(verdict, check, self.scrub(detail), self.scrub(breaks))
        self.results.append(r)
        self._emit(r)
        return r

    def scrub(self, text: str) -> str:
        """Never, under any circumstance, let the token secret reach stdout."""
        if self.secret and self.secret in text:
            text = text.replace(self.secret, "<REDACTED-TOKEN-SECRET>")
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


# --------------------------------------------------------------------------- #
# TLS pinning — mirrors backend/hlidskjalf/pve.py::make_pinned_ssl_context
# --------------------------------------------------------------------------- #

class FingerprintMismatch(ssl.SSLError):
    pass


def normalize_fp(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").lower()


def check_pinned_cert(der: bytes | None, expected: str) -> None:
    """Raise FingerprintMismatch unless `der` hashes (SHA-256) to `expected`."""
    if der is None or hashlib.sha256(der).hexdigest() != expected:
        raise FingerprintMismatch(
            "PVE certificate SHA-256 fingerprint does not match the pinned value"
        )


def make_pinned_ssl_context(fingerprint: str) -> ssl.SSLContext:
    """Byte-for-byte the same policy as pve.py: accept exactly one certificate.

    The pin is wired into BOTH handshake paths: `sslobject_class` for the
    memory-BIO path (wrap_bio — asyncio/httpx/websockets, what the panel uses)
    and `sslsocket_class` for the plain-socket path (wrap_socket), which
    `sslobject_class` does NOT cover.
    """
    expected = normalize_fp(fingerprint)

    class PinnedSSLObject(ssl.SSLObject):
        def do_handshake(self) -> None:  # type: ignore[override]
            super().do_handshake()
            check_pinned_cert(self.getpeercert(binary_form=True), expected)

    class PinnedSSLSocket(ssl.SSLSocket):
        def do_handshake(self, *args, **kwargs) -> None:  # type: ignore[override]
            super().do_handshake(*args, **kwargs)
            check_pinned_cert(self.getpeercert(binary_form=True), expected)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.sslobject_class = PinnedSSLObject
    ctx.sslsocket_class = PinnedSSLSocket
    return ctx


def observe_cert_fingerprint(host: str, port: int, timeout: float = 10.0) -> str:
    """SHA-256 of the DER cert the host actually presents, colon-separated hex."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


# --------------------------------------------------------------------------- #
# Tiny PVE client (same auth header + same TLS policy as pve.py)
# --------------------------------------------------------------------------- #

class Pve:
    def __init__(self, args, report: Report):
        self.a = args
        self.report = report
        self.ssl_context: ssl.SSLContext | None = None
        if args.scheme == "https":
            if not args.fingerprint:
                # pve.py *refuses* to connect. We keep going (unverified) purely so the
                # rest of the checks can still run, and we FAIL the pin check loudly.
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self.ssl_context = ctx
            else:
                self.ssl_context = make_pinned_ssl_context(args.fingerprint)
        self.client = httpx.AsyncClient(
            base_url=f"{args.scheme}://{args.host}:{args.port}/api2/json",
            headers={"Authorization": f"PVEAPIToken={args.token_id}={args.token_secret}"},
            verify=self.ssl_context if self.ssl_context else False,
            timeout=httpx.Timeout(args.timeout, connect=10.0),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, path: str, **kw) -> tuple[int, Any, str]:
        """-> (status, data, error). Never raises; the caller decides the verdict."""
        try:
            resp = await self.client.request(method, path, **kw)
        except Exception as e:  # TLS pin mismatch, DNS, refused, timeout...
            return 0, None, f"{type(e).__name__}: {e}"
        if resp.status_code >= 400:
            msg = resp.reason_phrase
            try:
                body = resp.json()
                if body.get("errors"):
                    msg = f"{msg}: {body['errors']}"
                elif body.get("message"):
                    msg = f"{msg}: {body['message']}"
            except Exception:
                pass
            return resp.status_code, None, msg
        try:
            return resp.status_code, resp.json().get("data"), ""
        except Exception as e:
            return resp.status_code, None, f"response was not JSON: {e}"

    async def get(self, path: str, **params) -> tuple[int, Any, str]:
        return await self.call("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, **data) -> tuple[int, Any, str]:
        return await self.call("POST", path, data={k: v for k, v in data.items() if v is not None})


def missing_keys(obj: dict, keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if k not in obj]


def null_keys(obj: dict, keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if obj.get(k) is None]


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

async def check_tls(pve: Pve, rep: Report) -> None:
    a = pve.a
    rule("TLS certificate pinning  (backend/hlidskjalf/pve.py)")

    if a.scheme != "https":
        rep.add(SKIP, "tls/pin", "--scheme http: no TLS to pin. Real deployments MUST use https.")
        return

    try:
        observed = observe_cert_fingerprint(a.host, a.port)
    except Exception as e:
        rep.add(FAIL, "tls/reachable", f"could not complete a TLS handshake with "
                                       f"{a.host}:{a.port} — {type(e).__name__}: {e}")
        return
    rep.add(INFO, "tls/observed-sha256", observed)

    if not a.fingerprint:
        rep.add(
            FAIL, "tls/pin",
            "no --fingerprint supplied. pve.py REFUSES https without one.",
            "backend/hlidskjalf/pve.py:64 raises RuntimeError("
            "'HLIDSKJALF_PVE_FINGERPRINT is required with https').\n"
            f"Set HLIDSKJALF_PVE_FINGERPRINT to the value above, then re-run "
            f"with --fingerprint to confirm it pins.",
        )
        return

    if normalize_fp(a.fingerprint) == normalize_fp(observed):
        rep.add(PASS, "tls/pin", "supplied fingerprint matches the cert the host presents")
    else:
        rep.add(
            FAIL, "tls/pin",
            f"MISMATCH. supplied={a.fingerprint}  observed={observed}",
            "The panel will refuse every request (FingerprintMismatch in pve.py).\n"
            "Re-read it on the host: openssl x509 -in /etc/pve/local/pve-ssl.pem "
            "-noout -fingerprint -sha256",
        )
        return

    # Negative test: a WRONG pin must abort the handshake on BOTH handshake
    # paths. (a) the memory-BIO path (wrap_bio via asyncio — what the panel and
    # httpx actually use, enforced by sslobject_class) and (b) the plain-socket
    # path (wrap_socket, enforced ONLY because sslsocket_class is set — the
    # first real-hardware run proved sslobject_class alone does not cover it).
    # If either completes, the pinning is decorative and a MITM sails through.
    bogus = "00" * 32
    ctx = make_pinned_ssl_context(bogus)
    completed: list[str] = []
    inconclusive: list[str] = []

    try:  # (a) async / memory-BIO — the panel's path
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(a.host, a.port, ssl=ctx), timeout=10
        )
        writer.close()
        completed.append("wrap_bio (asyncio/httpx — the panel's path)")
    except (FingerprintMismatch, ssl.SSLError):
        pass  # refused, as designed
    except Exception as e:
        inconclusive.append(f"wrap_bio: {type(e).__name__}: {e}")

    try:  # (b) sync / plain-socket
        with socket.create_connection((a.host, a.port), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=a.host):
                pass
        completed.append("wrap_socket (plain-socket path)")
    except (FingerprintMismatch, ssl.SSLError):
        pass  # refused, as designed
    except Exception as e:
        inconclusive.append(f"wrap_socket: {type(e).__name__}: {e}")

    if completed:
        rep.add(FAIL, "tls/pin-rejects-wrong",
                "a deliberately WRONG fingerprint still completed the handshake "
                f"via {', '.join(completed)}",
                "The pin is not actually enforced on that path — this is a MITM hole.\n"
                "wrap_socket only pins because pve.py sets ctx.sslsocket_class; "
                "sslobject_class alone does NOT cover it. A regression in either "
                "hook silently disables pinning for every caller of that API.")
    elif inconclusive:
        rep.add(WARN, "tls/pin-rejects-wrong", "inconclusive — " + "; ".join(inconclusive))
    else:
        rep.add(PASS, "tls/pin-rejects-wrong",
                "a wrong fingerprint aborts the handshake on both paths "
                "(wrap_bio and wrap_socket), as designed")


async def check_auth(pve: Pve, rep: Report) -> None:
    rule("Authentication  (PVEAPIToken header form)")
    status, data, err = await pve.get("/version")
    if status == 404:
        # The route does not exist (the dev mock implements no /version) — that is not an
        # auth failure. Re-probe with an endpoint we know exists to settle it.
        status2, _, err2 = await pve.get("/nodes")
        if err2:
            rep.add(FAIL, "auth/token",
                    f"GET /version -> 404 and GET /nodes -> HTTP {status2}: {err2}",
                    "Header form is `Authorization: PVEAPIToken=<user@realm!tokenid>=<secret>` "
                    "(pve.py:77).\nNothing below this line can be trusted.")
            return
        rep.add(PASS, "auth/token",
                f"authenticated as {pve.a.token_id} (no /version endpoint — this is not real "
                "Proxmox, presumably dev/mock_pve.py)")
    elif err:
        rep.add(
            FAIL, "auth/token", f"GET /version -> HTTP {status or 'no response'}: {err}",
            "Header form is `Authorization: PVEAPIToken=<user@realm!tokenid>=<secret>` "
            "(pve.py:77).\n"
            "401 => wrong token id or secret. The token id must be the FULL "
            "'user@realm!tokenname'.\n"
            "Nothing below this line can be trusted.",
        )
        return
    else:
        rep.add(PASS, "auth/token", f"authenticated as {pve.a.token_id} -> "
                                    f"{(data or {}).get('version')} / "
                                    f"{(data or {}).get('release', '?')}")

    # Effective privileges. The classic footgun: `--privsep 1` gives the TOKEN no
    # privileges of its own, so it authenticates fine and then 403s on everything.
    status, perms, err = await pve.get("/access/permissions")
    if err:
        rep.add(WARN, "auth/permissions", f"GET /access/permissions -> HTTP {status}: {err} "
                                          "(cannot introspect the token's privileges)")
        return
    perms = perms or {}
    root = perms.get("/", {}) or {}
    granted = sorted(k for k, v in root.items() if v)
    if not perms:
        rep.add(
            FAIL, "auth/permissions", "the token has NO privileges anywhere",
            "Almost certainly `pveum user token add ... --privsep 1` with no ACL on the "
            "token.\nEither re-create with --privsep 0, or grant the token itself an ACL.",
        )
        return
    scope = ", ".join(f"{p} ({len(v)} privs)" for p, v in list(perms.items())[:4])
    rep.add(INFO, "auth/permissions", f"token holds privileges on: {scope}")

    # What each panel feature actually needs.
    needed = {
        "VM.Audit": "list VMs, read config (/api/vms)",
        "Sys.Audit": "GET /nodes, node status, task log (setup wizard + admin views)",
        "Datastore.Audit": "GET /nodes/<node>/storage",
        "VM.Console": "noVNC console (POST vncproxy)",
        "VM.Monitor": "QEMU guest-agent IP discovery",
        "VM.PowerMgmt": "start / stop / reboot",
        "VM.Config.Disk": "provision (resize), reinstall",
        "VM.Allocate": "provision (clone), destroy, reinstall",
        "VM.Clone": "provision (clone from template)",
    }
    have = {p for scope_perms in perms.values() for p, v in (scope_perms or {}).items() if v}
    lacking = [f"{p} [{why}]" for p, why in needed.items() if p not in have]
    if lacking:
        rep.add(WARN, "auth/privileges", "token lacks: " + "; ".join(lacking),
                "Panel features depending on these will 403 at runtime. "
                "PVEAuditor alone is NOT enough for console/power/provision.")
    else:
        rep.add(PASS, "auth/privileges", f"token holds every privilege the panel uses "
                                         f"({len(granted)} on /)")


async def check_nodes(pve: Pve, rep: Report) -> list[str]:
    rule("GET /nodes with a scoped token  (the setup wizard depends on this)")
    status, nodes, err = await pve.get("/nodes")
    if err:
        rep.add(
            FAIL, "nodes/list", f"GET /nodes -> HTTP {status}: {err}",
            "routes/setup.py:96 calls client.get('/nodes') and aborts setup if it fails.\n"
            "First-run is BROKEN for every operator using a token with these privileges.\n"
            "Needs Sys.Audit on / (PVEAuditor grants it).",
        )
        return []
    names = [n.get("node") for n in (nodes or []) if n.get("node")]
    rep.add(PASS, "nodes/list", f"scoped non-root token CAN call /nodes -> {names}")

    if not names:
        rep.add(FAIL, "nodes/list", "empty node list", "the setup wizard will refuse every node name.")
        return []

    if len(names) > 1:
        rep.add(
            FAIL, "nodes/single-node", f"THIS IS A CLUSTER: {len(names)} nodes {names}",
            "KNOWN LIMITATION. The panel pins one node (config.py: pve_node) and builds\n"
            "every guest path as /nodes/<pve_node>/<kind>/<vmid> (pve.py:_guest_base).\n"
            "Guests on the other nodes appear in /cluster/resources (so they show up in the\n"
            "fleet list and in the accumulator) but EVERY detail / power / console / metrics\n"
            "call against them hits the wrong node and fails. Do not run the panel on a\n"
            "cluster until this is fixed.",
        )
    else:
        rep.add(PASS, "nodes/single-node", f"single node '{names[0]}' — matches the panel's model")

    if pve.a.node not in names:
        rep.add(FAIL, "nodes/configured-node",
                f"--node '{pve.a.node}' is not on this host. Found: {names}",
                "HLIDSKJALF_PVE_NODE must be one of the above or every node-scoped call 404s.")
    else:
        rep.add(PASS, "nodes/configured-node", f"'{pve.a.node}' exists")
    return names


async def check_cluster_resources(pve: Pve, rep: Report, nodes: list[str]) -> list[dict]:
    rule("GET /cluster/resources  (fleet list, accumulator, frontend types.ts)")

    # The panel always passes type=vm (pve.py:cluster_resources).
    status, filtered, err = await pve.get("/cluster/resources", type="vm")
    if err:
        rep.add(FAIL, "resources/list", f"GET /cluster/resources?type=vm -> HTTP {status}: {err}",
                "pve.py:cluster_resources — the fleet list, the bandwidth accumulator and\n"
                "every _ensure_vm_access lookup are dead without this.")
        return []
    filtered = filtered or []
    rep.add(PASS, "resources/list", f"type=vm returned {len(filtered)} guest(s)")

    kinds = {r.get("type") for r in filtered}
    if "lxc" in kinds:
        rep.add(PASS, "resources/lxc-included", "type=vm includes LXC containers (type='lxc')")
    else:
        _, unfiltered, _ = await pve.get("/cluster/resources")
        lxc_elsewhere = [r for r in (unfiltered or []) if r.get("type") == "lxc"]
        if lxc_elsewhere:
            rep.add(FAIL, "resources/lxc-included",
                    f"type=vm hid {len(lxc_elsewhere)} LXC guest(s) that the unfiltered call shows",
                    "pve.py:cluster_resources(type_='vm') — LXC guests would silently vanish\n"
                    "from the fleet list and from bandwidth accounting.")
        else:
            rep.add(INFO, "resources/lxc-included", "no LXC guests on this host — cannot tell")

    templates = [r for r in filtered if r.get("template") == 1]
    if templates:
        rep.add(PASS, "resources/templates-included",
                f"type=vm includes {len(templates)} template(s) — routes/provision.py can see them")
    else:
        rep.add(WARN, "resources/templates-included",
                "no template=1 guests visible",
                "routes/provision.py:list_templates returns []; the provision form will have an\n"
                "empty template picker. Create a template, or provisioning is unusable.")

    # The exact fields frontend/src/types.ts::VmListItem and routes/vms.py read.
    required = ("vmid", "name", "type", "status", "maxcpu", "maxmem", "maxdisk", "netin", "netout")
    bad: list[str] = []
    for r in filtered:
        gaps = missing_keys(r, required)
        if gaps:
            bad.append(f"vmid {r.get('vmid')} ({r.get('type')}) missing {gaps}")
    if bad:
        rep.add(FAIL, "resources/shape", "; ".join(bad[:5]),
                "frontend/src/types.ts::VmListItem declares these non-optional, and\n"
                "routes/vms.py::list_vms copies them straight through. Missing => nulls in the UI.")
    else:
        rep.add(PASS, "resources/shape", f"every guest has {', '.join(required)}")

    # netin/netout must be cumulative byte counters or the accumulator books nonsense.
    stopped_with_counters = [r for r in filtered
                             if r.get("status") != "running" and (r.get("netin") or 0) > 0]
    running = [r for r in filtered if r.get("status") == "running" and r.get("template") != 1]
    if running:
        sample = running[0]
        rep.add(INFO, "resources/counters",
                f"vmid {sample.get('vmid')} netin={sample.get('netin')} netout={sample.get('netout')} "
                f"(accumulator.py treats these as cumulative bytes since guest start)")
    if stopped_with_counters:
        rep.add(WARN, "resources/counters",
                f"{len(stopped_with_counters)} stopped guest(s) report non-zero netin — "
                "accumulator.py assumes counters reset to 0 on stop/start")

    # The mock fabricates disk usage for QEMU. Real PVE does not.
    qemu_running = [r for r in running if r.get("type") == "qemu"]
    if qemu_running:
        zero_disk = [r for r in qemu_running if not r.get("disk")]
        if zero_disk:
            rep.add(
                WARN, "resources/qemu-disk",
                f"{len(zero_disk)}/{len(qemu_running)} running QEMU guests report disk=0",
                "EXPECTED against real PVE — it does not know a VM's in-guest disk usage.\n"
                "dev/mock_pve.py:93 fabricates disk = 45% of maxdisk, so the UI's disk bar\n"
                "looks plausible in dev and will read 0% / empty on real hardware.\n"
                "Cosmetic, but the mock is lying. (LXC does report real disk usage.)",
            )
        else:
            rep.add(PASS, "resources/qemu-disk", "QEMU guests report a non-zero disk figure")

    # Guests living on a node other than the configured one.
    if len(nodes) > 1:
        strays = [r for r in filtered if r.get("node") and r.get("node") != pve.a.node]
        if strays:
            ids = ", ".join(f"{r.get('vmid')}@{r.get('node')}" for r in strays[:8])
            rep.add(FAIL, "resources/off-node-guests",
                    f"{len(strays)} guest(s) are NOT on '{pve.a.node}': {ids}",
                    "These appear in the fleet list but every /nodes/<pve_node>/... call for them\n"
                    "will 500/595. See routes/vms.py::vm_detail, console.py, metrics.py.")
    return filtered


async def check_node_status(pve: Pve, rep: Report) -> None:
    rule("Node status + storage  (routes/metrics.py::node_info)")
    node = pve.a.node

    status, raw, err = await pve.get(f"/nodes/{node}/status")
    if err:
        rep.add(FAIL, "node/status", f"GET /nodes/{node}/status -> HTTP {status}: {err}",
                "routes/metrics.py:55 — /api/node (the admin dashboard header) is dead.")
    else:
        raw = raw or {}
        mem, cpuinfo = raw.get("memory"), raw.get("cpuinfo")
        nested_mem = isinstance(mem, dict) and mem.get("total") is not None
        has_cpus = isinstance(cpuinfo, dict) and cpuinfo.get("cpus") is not None
        flat = {k: raw.get(k) for k in ("maxcpu", "mem", "maxmem") if raw.get(k) is not None}

        rep.add(PASS if nested_mem else FAIL, "node/memory-nested",
                f"memory={'nested {used,total}' if nested_mem else repr(mem)}",
                "" if nested_mem else
                "routes/metrics.py:62 does `raw.get('memory') or {}` then reads .used/.total.\n"
                "The admin dashboard's RAM gauge will be null.")
        rep.add(PASS if has_cpus else FAIL, "node/cpuinfo-cpus",
                f"cpuinfo.cpus={cpuinfo.get('cpus') if isinstance(cpuinfo, dict) else None}",
                "" if has_cpus else
                "routes/metrics.py:66 falls back to cpuinfo.cpus for maxcpu. Core count will be null.")
        rep.add(INFO, "node/flat-fields",
                f"flat maxcpu/mem/maxmem present: {flat or 'none (normalization is doing the work)'}")

        rootfs = raw.get("rootfs")
        rep.add(PASS if isinstance(rootfs, dict) else WARN, "node/rootfs",
                f"rootfs={'{used,total}' if isinstance(rootfs, dict) else repr(rootfs)} "
                "(frontend/src/types.ts::NodeInfo.status.rootfs)")
        rep.add(INFO, "node/loadavg", f"loadavg={raw.get('loadavg')!r} "
                                      "(types.ts allows number[] | string[])")

    status, storages, err = await pve.get(f"/nodes/{node}/storage")
    if err:
        rep.add(FAIL, "node/storage", f"GET /nodes/{node}/storage -> HTTP {status}: {err}",
                "routes/metrics.py:56 — needs Datastore.Audit. The storage panel is dead.")
        return
    storages = storages or []
    required = ("storage", "type", "used", "total", "avail", "content")
    gaps = {k for s in storages for k in missing_keys(s, required)}
    if gaps:
        rep.add(WARN, "node/storage", f"{len(storages)} storage(s); some lack {sorted(gaps)}",
                "frontend/src/types.ts::NodeStorage declares used/total/avail non-optional.\n"
                "(Inactive/offline storages legitimately omit them — check `active`.)")
    else:
        rep.add(PASS, "node/storage",
                f"{len(storages)} storage(s), all with {', '.join(required)}: "
                + ", ".join(s.get("storage", "?") for s in storages))

    names = [s.get("storage") for s in storages]
    if pve.a.clone_storage not in names:
        rep.add(WARN, "node/clone-storage",
                f"HLIDSKJALF_CLONE_STORAGE default '{pve.a.clone_storage}' is not on this host "
                f"({names})",
                "routes/provision.py passes it as `storage=` to every clone. Set it correctly\n"
                "or every provision will fail.")
    else:
        rep.add(PASS, "node/clone-storage", f"clone storage '{pve.a.clone_storage}' exists")


async def check_rrd(pve: Pve, rep: Report, guest: dict | None) -> None:
    rule("rrddata  (datasources/rrd.py, frontend Vm/Node metric charts)")
    node = pve.a.node
    # datasources/rrd.py
    VM_FIELDS = ("cpu", "maxcpu", "mem", "maxmem", "disk", "maxdisk",
                 "diskread", "diskwrite", "netin", "netout")
    NODE_FIELDS = ("cpu", "maxcpu", "memused", "memtotal", "iowait",
                   "netin", "netout", "loadavg", "rootused", "roottotal")
    # frontend/src/types.ts::Timeframe (the panel also allows "year" server-side)
    TIMEFRAMES = ("hour", "day", "week", "month")

    async def probe(label: str, path: str, fields: tuple[str, ...]) -> None:
        for tf in TIMEFRAMES:
            status, rows, err = await pve.get(path, timeframe=tf, cf="AVERAGE")
            if err:
                rep.add(FAIL, f"rrd/{label}/{tf}", f"HTTP {status}: {err}",
                        f"datasources/rrd.py — the {label} chart for '{tf}' is dead.")
                continue
            rows = rows or []
            if not rows:
                rep.add(WARN, f"rrd/{label}/{tf}", "0 rows (no RRD history yet?)")
                continue
            last = rows[-1]
            if "time" not in last:
                rep.add(FAIL, f"rrd/{label}/{tf}", f"rows have no 'time' key: {sorted(last)}",
                        "datasources/rrd.py:_shape maps r['time'] -> 't'. Every x-axis is null.")
                continue
            gone = missing_keys(last, fields)
            nulls = null_keys(last, tuple(f for f in fields if f not in gone))
            if gone:
                rep.add(FAIL, f"rrd/{label}/{tf}", f"{len(rows)} rows; MISSING {gone}",
                        f"datasources/rrd.py:{'VM_FIELDS' if label == 'guest' else 'NODE_FIELDS'} "
                        f"expects these; they will serialize as null and the chart line vanishes.")
            elif nulls:
                rep.add(WARN, f"rrd/{label}/{tf}", f"{len(rows)} rows; all fields present, "
                                                   f"but null in the newest row: {nulls}")
            else:
                rep.add(PASS, f"rrd/{label}/{tf}", f"{len(rows)} rows, all {len(fields)} fields present")

    await probe("node", f"/nodes/{node}/rrddata", NODE_FIELDS)
    if guest:
        vmid, kind = guest["vmid"], ("lxc" if guest.get("type") == "lxc" else "qemu")
        rep.add(INFO, "rrd/guest", f"probing vmid {vmid} ({kind})")
        await probe("guest", f"/nodes/{node}/{kind}/{vmid}/rrddata", VM_FIELDS)
    else:
        rep.add(SKIP, "rrd/guest", "no guest available to probe")


async def check_guest_detail(pve: Pve, rep: Report, guest: dict | None) -> None:
    rule("Guest status/current + config + guest agent  (routes/vms.py::vm_detail)")
    if not guest:
        rep.add(SKIP, "guest/*", "no running guest to probe")
        return
    node = pve.a.node
    vmid = guest["vmid"]
    kind = "lxc" if guest.get("type") == "lxc" else "qemu"

    status, cur, err = await pve.get(f"/nodes/{node}/{kind}/{vmid}/status/current")
    if err:
        rep.add(FAIL, "guest/status-current", f"HTTP {status}: {err}",
                "routes/vms.py:93 — the VM detail page is dead.")
        return
    cur = cur or {}
    wanted = ("status", "uptime", "cpu", "mem", "maxmem", "netin", "netout", "diskread", "diskwrite")
    gaps = missing_keys(cur, wanted)
    rep.add(PASS if not gaps else WARN, "guest/status-current",
            f"vmid {vmid}: {'all detail fields present' if not gaps else f'missing {gaps}'}",
            "" if not gaps else "routes/vms.py::vm_detail reads these; they become null in VmDetail.")

    if kind == "qemu":
        cpus = cur.get("cpus")
        rep.add(PASS if cpus is not None else WARN, "guest/cpus",
                f"status/current.cpus={cpus!r} "
                "(routes/vms.py:123 uses it for maxcpu, falling back to resource.maxcpu)")

        # routes/vms.py:97 gates the agent call on `current.get("agent")` being truthy.
        agent_flag = cur.get("agent")
        rep.add(INFO, "guest/agent-flag", f"status/current.agent={agent_flag!r} "
                                          "(routes/vms.py:97 only calls the agent when this is truthy)")

        status, ag, err = await pve.get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        if err:
            # The panel catches PveError and falls back to ipconfig0. Any 4xx/5xx is FINE
            # as long as it is an HTTP error and not a hang/crash.
            if status and status >= 400:
                rep.add(PASS, "guest/agent-degrades",
                        f"agent not answering: HTTP {status} ({err}) — routes/vms.py:106 catches "
                        f"PveError and falls back to ipconfig0. Degrades, does not 500.")
            else:
                rep.add(FAIL, "guest/agent-degrades", f"no HTTP response at all: {err}",
                        "routes/vms.py:106 only catches PveError. A transport-level hang here\n"
                        "makes the VM detail page hang or 500.")
        else:
            result = (ag or {}).get("result")
            if not isinstance(result, list):
                rep.add(FAIL, "guest/agent-shape",
                        f"expected {{'result': [...]}}, got keys {sorted((ag or {}))}",
                        "routes/vms.py:100 does `(agent or {}).get('result', [])` — IP discovery\n"
                        "silently yields no IPs.")
                return
            ips, shape_ok = [], True
            for iface in result:
                if "name" not in iface or "ip-addresses" not in iface:
                    shape_ok = False
                    continue
                for addr in iface.get("ip-addresses") or []:
                    if addr.get("ip-address-type") == "ipv4" and iface.get("name") != "lo":
                        ips.append(addr.get("ip-address"))
            rep.add(PASS if shape_ok else FAIL, "guest/agent-shape",
                    f"{len(result)} interface(s); ipv4 discovered: {ips or 'none'}",
                    "" if shape_ok else
                    "routes/vms.py:101-105 expects iface['name'] and iface['ip-addresses'][]\n"
                    "with 'ip-address' + 'ip-address-type'.")

    status, cfg, err = await pve.get(f"/nodes/{node}/{kind}/{vmid}/config")
    if err:
        rep.add(FAIL, "guest/config", f"HTTP {status}: {err}", "routes/vms.py:94")
        return
    cfg = cfg or {}
    net0 = cfg.get("net0", "")
    parts = dict(p.split("=", 1) for p in net0.split(",") if "=" in p)
    mac = next((v for k, v in parts.items() if k in ("virtio", "e1000", "vmxnet3", "rtl8139")), None)
    rep.add(PASS if net0 else WARN, "guest/net0",
            f"net0={net0!r} -> bridge={parts.get('bridge')} tag={parts.get('tag')} mac={mac}",
            "" if net0 else "routes/vms.py::_parse_net0 yields nothing; VLAN/MAC/bridge show blank.")
    rep.add(INFO, "guest/config-keys",
            f"cores={cfg.get('cores')!r} memory={cfg.get('memory')!r} "
            f"scsi0={str(cfg.get('scsi0'))[:48]!r} ipconfig0={cfg.get('ipconfig0')!r}")
    if kind == "qemu" and "scsi0" not in cfg:
        rep.add(WARN, "guest/scsi0",
                f"this VM has no scsi0 (disks: "
                f"{[k for k in cfg if k.startswith(('scsi', 'virtio', 'sata', 'ide'))]})",
                "routes/provision.py hardcodes scsi0 for resize and for reading the template's\n"
                "disk size. A template that uses virtio0/sata0 will never be resized correctly.")


async def check_tasks_and_upid(pve: Pve, rep: Report) -> None:
    rule("Tasks + UPID parsing  (SECURITY-CRITICAL — routes/vms.py::_vmid_from_upid)")
    node = pve.a.node

    status, tasks, err = await pve.get(f"/nodes/{node}/tasks", limit=50)
    if err:
        rep.add(FAIL, "tasks/list", f"GET /nodes/{node}/tasks -> HTTP {status}: {err}",
                "routes/vms.py:218 (/api/tasks/recent). Needs Sys.Audit on /nodes/<node>.")
        return
    tasks = tasks or []
    if not tasks:
        rep.add(WARN, "tasks/list", "the task log is EMPTY — cannot validate real UPIDs",
                "Re-run with --allow-writes --vmid <scratch>=900+ to trigger a real task,\n"
                "or do anything in the Proxmox web UI first and re-run.")
        return
    rep.add(PASS, "tasks/list", f"{len(tasks)} task(s) in the log")

    shape = ("upid", "type", "id", "user", "starttime")
    gaps = {k for t in tasks for k in missing_keys(t, shape)}
    rep.add(PASS if not gaps else FAIL, "tasks/shape",
            f"list rows {'have' if not gaps else 'are MISSING'} {sorted(gaps) or list(shape)}",
            "" if not gaps else "frontend/src/types.ts::RecentTask declares upid/type/id/user/starttime.")

    # routes/vms.py::recent_tasks normalizes `status` vs `exitstatus`. Which shape is real?
    finished = [t for t in tasks if t.get("endtime")]
    if finished:
        t = finished[0]
        s, ex = t.get("status"), t.get("exitstatus")
        if ex is None and s not in (None, "running", "stopped"):
            rep.add(PASS, "tasks/status-vs-exitstatus",
                    f"list puts the RESULT in `status` ({s!r}) and has no `exitstatus` — "
                    "routes/vms.py:229 normalization is correct and NECESSARY")
        elif s in ("running", "stopped"):
            rep.add(INFO, "tasks/status-vs-exitstatus",
                    f"list uses status={s!r} exitstatus={ex!r} (already the panel's shape)")
        else:
            rep.add(WARN, "tasks/status-vs-exitstatus",
                    f"unrecognised combination status={s!r} exitstatus={ex!r}",
                    "routes/vms.py:225-236 may normalize this wrong; the Tasks tab shows a bad state.")

    # ---- The one that can fail open -----------------------------------------
    guest_tasks = [t for t in tasks if str(t.get("id", "")).isdigit()]
    node_tasks = [t for t in tasks if not str(t.get("id", "")).isdigit()]
    sample = (guest_tasks or tasks)[0]
    upid = sample["upid"]
    parts = upid.split(":")
    rep.add(INFO, "upid/example", f"{upid}   ({len(parts)} colon-separated fields)")

    def panel_parse(u: str) -> int | None:
        p = u.split(":")
        if len(p) < 7:
            return None
        try:
            return int(p[6])
        except ValueError:
            return None

    ok, wrong = 0, []
    for t in guest_tasks:
        want = int(t["id"])
        got = panel_parse(t["upid"])
        if got == want:
            ok += 1
        else:
            wrong.append((t["upid"], want, got))

    if guest_tasks and not wrong:
        rep.add(PASS, "upid/vmid-field",
                f"parts[6] == the guest id for all {ok} guest task(s) — "
                "routes/vms.py::_vmid_from_upid is CORRECT against real Proxmox")
    elif wrong:
        u, want, got = wrong[0]
        # Where does the vmid actually live in the UPIDs THIS host emits?
        p = u.split(":")
        idx = next((i for i, v in enumerate(p) if v == str(want)), None)
        # Genuine PVE emits 8 fields + a trailing empty one => 9 after split(':').
        odd = (f"NOTE: these UPIDs have {len(p)} fields; genuine Proxmox emits 9 "
               f"(UPID:node:pid:pstart:starttime:dtype:id:user: — note the trailing ':').\n"
               f"      A different count means this peer is NOT real Proxmox (the dev mock\n"
               f"      omits `pstart`). Do NOT 'fix' the panel to match a mock — fix the mock.\n"
               if len(p) != 9 else
               f"The vmid sits at parts[{idx}], not parts[6]. Fix _vmid_from_upid accordingly,\n"
               f"and fix dev/mock_pve.py::_mk_upid to emit the same format — otherwise the test\n"
               f"suite goes green against the wrong thing.\n")
        rep.add(
            FAIL, "upid/vmid-field",
            f"_vmid_from_upid('{u}') = {got!r}, but the task's id is {want}. "
            f"({len(wrong)}/{len(guest_tasks)} wrong; in these UPIDs the vmid sits at "
            f"parts[{idx}])",
            "routes/vms.py:173-185 assumes UPID:node:pid:pstart:starttime:dtype:id:user:\n"
            "and reads parts[6] as the vmid to AUTHORIZE /api/tasks/<upid>/status.\n"
            "Consequences, both bad:\n"
            "  - returns None  -> every guest task is treated as a node task -> admins only,\n"
            "                     so regular users get 403 polling their own power action.\n"
            "  - returns a DIFFERENT numeric field -> the authorization check compares the\n"
            "                     wrong number: a tenant can craft a UPID whose parts[6] is\n"
            "                     THEIR vmid while the real task belongs to someone else.\n"
            "                     That is an IDOR on the task log. FAILS OPEN.\n"
            + odd,
        )
    else:
        rep.add(WARN, "upid/vmid-field", "no guest-scoped tasks in the log to check")

    for t in node_tasks[:1]:
        got = panel_parse(t["upid"])
        rep.add(PASS if got is None else FAIL, "upid/node-task",
                f"node task id={t.get('id')!r} -> _vmid_from_upid={got!r} "
                f"({'correctly admin-only' if got is None else 'MISPARSED as a vmid'})",
                "" if got is None else
                "routes/vms.py:198 would route a node-level task through _ensure_vm_access\n"
                "for a guest that has nothing to do with it.")

    # /tasks/<upid>/status — note the UPID goes into the URL PATH, colons and all.
    status, st, err = await pve.get(f"/nodes/{node}/tasks/{upid}/status")
    if err:
        rep.add(FAIL, "tasks/status", f"GET /nodes/{node}/tasks/<upid>/status -> HTTP {status}: {err}",
                "pve.py:task_status interpolates the raw UPID (with its colons and trailing ':')\n"
                "into the path. If this 400s, every task poll and every wait_task() is broken —\n"
                "which means provision/reinstall/destroy never complete.")
        return
    st = st or {}
    s, ex = st.get("status"), st.get("exitstatus")
    if s not in ("running", "stopped"):
        rep.add(FAIL, "tasks/status", f"status={s!r} (expected 'running' or 'stopped')",
                "pve.py:wait_task loops until status=='stopped' — it will spin until it times out\n"
                "(504) on every clone/destroy. frontend/src/types.ts::TaskStatus also assumes this.")
    else:
        rep.add(PASS, "tasks/status",
                f"status={s!r} exitstatus={ex!r} — pve.py:wait_task's "
                f"(status=='stopped' && exitstatus=='OK') contract holds")
    rep.add(INFO, "tasks/status-keys", f"keys: {sorted(st)}")


async def check_console(pve: Pve, rep: Report, guest: dict | None) -> None:
    rule("noVNC console  (THE BIGGEST UNKNOWN — routes/console.py has never met a real VNC server)")
    if pve.a.no_console:
        rep.add(SKIP, "console/*", "--no-console")
        return
    if not guest:
        rep.add(SKIP, "console/*", "no RUNNING guest — console_ticket 409s on a stopped guest")
        return

    node = pve.a.node
    vmid = guest["vmid"]
    kind = "lxc" if guest.get("type") == "lxc" else "qemu"

    # The only POST the default (read-only) path makes. Spawns a short-lived proxy
    # process on the PVE host; changes no guest state.
    status, data, err = await pve.post(f"/nodes/{node}/{kind}/{vmid}/vncproxy", websocket=1)
    if err:
        rep.add(FAIL, "console/vncproxy", f"POST vncproxy -> HTTP {status}: {err}",
                "routes/console.py:67. HTTP 403 => the token lacks VM.Console (PVEAuditor does\n"
                "NOT grant it). The console button is dead for every user.")
        return
    data = data or {}
    gaps = missing_keys(data, ("port", "ticket"))
    if gaps:
        rep.add(FAIL, "console/vncproxy", f"response lacks {gaps}; keys={sorted(data)}",
                "routes/console.py:70 does data['port'] and data['ticket'] — a KeyError => 500.")
        return
    port, ticket = str(data["port"]), data["ticket"]
    # The ticket is a credential: report its shape, never its value.
    rep.add(PASS, "console/vncproxy",
            f"port={port} ticket=<{len(ticket)} chars, redacted> keys={sorted(data)}")

    if websockets is None:
        rep.add(SKIP, "console/websocket", "the `websockets` package is not installed")
        return

    scheme = "wss" if pve.a.scheme == "https" else "ws"
    url = (f"{scheme}://{pve.a.host}:{pve.a.port}"
           f"/api2/json/nodes/{node}/{kind}/{vmid}/vncwebsocket"
           f"?port={port}&vncticket={urllib.parse.quote(ticket, safe='')}")
    headers = {"Authorization": f"PVEAPIToken={pve.a.token_id}={pve.a.token_secret}"}

    connect_kw: dict[str, Any] = {"subprotocols": ["binary"], "max_size": None}
    if scheme == "wss":
        connect_kw["ssl"] = pve.ssl_context
    try:
        import inspect
        sig = inspect.signature(websockets.connect)
        connect_kw["additional_headers" if "additional_headers" in sig.parameters
                   else "extra_headers"] = headers
    except (TypeError, ValueError):
        connect_kw["additional_headers"] = headers

    try:
        async with websockets.connect(url, **connect_kw) as ws:  # type: ignore[arg-type]
            rep.add(PASS, "console/websocket",
                    "handshake SUCCEEDED with the PVEAPIToken header and subprotocol 'binary' — "
                    "routes/console.py:122 can reach the upstream")
            # A real VNC server greets with "RFB 003.00x\n" unprompted. If we see it,
            # the byte pump has something real on the other end.
            try:
                first = await asyncio.wait_for(ws.recv(), timeout=8.0)
                if isinstance(first, str):
                    first = first.encode()
                if first.startswith(b"RFB "):
                    rep.add(PASS, "console/rfb-handshake",
                            f"upstream sent a real RFB greeting: {first[:12]!r} — there is a live "
                            "VNC server behind the websocket")
                else:
                    rep.add(WARN, "console/rfb-handshake",
                            f"first frame was not an RFB greeting: {first[:32]!r}",
                            "routes/console.py pumps raw bytes to noVNC. If this is not the RFB\n"
                            "protocol, noVNC will fail to negotiate and show a blank/failed screen.")
            except asyncio.TimeoutError:
                rep.add(WARN, "console/rfb-handshake",
                        "connected, but the upstream sent nothing in 8s",
                        "A real VNC server greets immediately. noVNC will hang on a blank canvas.\n"
                        "Verify by hand: open the console in the panel and type in it.")
    except Exception as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        rep.add(
            FAIL, "console/websocket",
            f"handshake FAILED: {type(e).__name__}: {e}" + (f" (HTTP {code})" if code else ""),
            "routes/console.py:122-128 dials vncwebsocket with ONLY the PVEAPIToken header.\n"
            "If Proxmox will not authenticate the websocket upgrade with an API token (it may\n"
            "insist on a PVEAuthCookie), the console is fundamentally broken and needs a\n"
            "redesign: /access/ticket to obtain a cookie, then dial with that.\n"
            "This is the single biggest unvalidated assumption in the panel.",
        )


async def check_writes(pve: Pve, rep: Report) -> None:
    """Opt-in. Triggers ONE real guest task on a scratch VM and validates its UPID."""
    rule("WRITE TESTS  (--allow-writes)")
    node, vmid = pve.a.node, pve.a.vmid

    status, res, err = await pve.get("/cluster/resources", type="vm")
    guest = next((r for r in (res or []) if r.get("vmid") == vmid), None)
    if not guest:
        rep.add(FAIL, "write/scratch-vm", f"no guest with vmid {vmid} on this host")
        return
    if guest.get("template") == 1:
        rep.add(FAIL, "write/scratch-vm", f"vmid {vmid} is a TEMPLATE — refusing to touch it")
        return
    kind = "lxc" if guest.get("type") == "lxc" else "qemu"
    name = guest.get("name")
    if guest.get("status") == "running":
        rep.add(SKIP, "write/power-cycle",
                f"scratch guest {vmid} ('{name}') is RUNNING. Refusing to stop something that is "
                "up. Stop it in the Proxmox UI and re-run, and the script will start it and put "
                "it back.")
        return

    rep.add(INFO, "write/scratch-vm", f"vmid {vmid} '{name}' ({kind}), currently stopped")

    status, upid, err = await pve.post(f"/nodes/{node}/{kind}/{vmid}/status/start")
    if err:
        rep.add(FAIL, "write/start", f"POST status/start -> HTTP {status}: {err}",
                "routes/vms.py:169. HTTP 403 => the token lacks VM.PowerMgmt.")
        return
    if not isinstance(upid, str) or not upid.startswith("UPID:"):
        rep.add(FAIL, "write/start", f"expected a UPID string, got {upid!r}",
                "routes/vms.py:170 returns {'upid': <this>} and the SPA polls it.")
        return
    rep.add(PASS, "write/start", f"real power-action UPID: {upid}")

    # THE check: does a UPID that PVE minted for a power action parse to this vmid?
    parts = upid.split(":")
    got = None
    if len(parts) >= 7:
        try:
            got = int(parts[6])
        except ValueError:
            got = None
    if got == vmid:
        rep.add(PASS, "write/upid-parse",
                f"_vmid_from_upid(<start upid>) == {vmid} — task-status authorization is sound")
    else:
        idx = next((i for i, v in enumerate(parts) if v == str(vmid)), None)
        rep.add(FAIL, "write/upid-parse",
                f"_vmid_from_upid = {got!r}, expected {vmid} (it actually sits at parts[{idx}])",
                "routes/vms.py:173-185 — see the upid/vmid-field failure above. Security-critical.")

    # Poll it the way pve.py::wait_task does.
    deadline = time.monotonic() + 60
    final: dict = {}
    while time.monotonic() < deadline:
        _, st, err = await pve.get(f"/nodes/{node}/tasks/{upid}/status")
        if err:
            rep.add(FAIL, "write/wait-task", f"polling the fresh UPID failed: {err}",
                    "pve.py:wait_task — clone/destroy will never complete.")
            break
        final = st or {}
        if final.get("status") == "stopped":
            break
        await asyncio.sleep(1.0)
    if final.get("status") == "stopped":
        exitstatus = final.get("exitstatus")
        rep.add(PASS if exitstatus == "OK" else WARN, "write/wait-task",
                f"task completed: status='stopped' exitstatus={exitstatus!r} — "
                "pve.py:wait_task's contract holds")
    elif final:
        rep.add(WARN, "write/wait-task", f"task did not stop within 60s (status={final.get('status')!r})")

    # Put it back exactly as we found it.
    await asyncio.sleep(3.0)
    status, upid2, err = await pve.post(f"/nodes/{node}/{kind}/{vmid}/status/stop")
    if err:
        rep.add(FAIL, "write/restore",
                f"COULD NOT STOP vmid {vmid} AGAIN -> HTTP {status}: {err}. "
                "IT IS STILL RUNNING — stop it yourself.")
    else:
        rep.add(PASS, "write/restore", f"scratch guest {vmid} stopped again (upid {upid2})")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def pick_guest(resources: list[dict], prefer_vmid: int | None) -> dict | None:
    """A running, non-template guest to probe read-only. QEMU preferred (more surface)."""
    candidates = [r for r in resources if r.get("template") != 1]
    if prefer_vmid is not None:
        hit = next((r for r in candidates if r.get("vmid") == prefer_vmid), None)
        if hit:
            return hit
    running = [r for r in candidates if r.get("status") == "running"]
    for pool in (running, candidates):
        qemu = [r for r in pool if r.get("type") == "qemu"]
        if qemu:
            return qemu[0]
        if pool:
            return pool[0]
    return None


def resolve_secret(args) -> str:
    if args.token_secret_file:
        return open(args.token_secret_file).read().strip()
    if args.token_secret:
        return args.token_secret
    env = os.environ.get("HLIDSKJALF_PVE_TOKEN_SECRET", "").strip()
    if env:
        return env
    if not sys.stdin.isatty():
        sys.exit("No token secret. Use --token-secret-file, HLIDSKJALF_PVE_TOKEN_SECRET, "
                 "or run interactively.")
    return getpass.getpass(f"API token secret for {args.token_id}: ").strip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate-proxmox.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Check Hlidskjalf's assumptions against a REAL Proxmox VE host.\n\n"
            "The panel's 163 tests all run against dev/mock_pve.py — a mock we wrote\n"
            "ourselves. They prove self-consistency, not correctness. This script talks to\n"
            "actual Proxmox and reports, per assumption, whether the panel is right.\n\n"
            "READ-ONLY by default. Nothing is created, destroyed, powered or reconfigured\n"
            "without --allow-writes AND --vmid <scratch vmid >= 900>."
        ),
        epilog=(
            "examples:\n"
            "  # read-only, against real hardware\n"
            "  python scripts/validate-proxmox.py --host 192.168.1.10 --node pve \\\n"
            "      --token-id 'hlidskjalf@pve!panel' --fingerprint AA:BB:...:FF\n\n"
            "  # against the local mock (proves the SCRIPT works; proves nothing about PVE)\n"
            "  python scripts/validate-proxmox.py --scheme http --host 127.0.0.1 --port 18006 \\\n"
            "      --node hella --token-secret mock-secret\n\n"
            "  # opt in to ONE power-cycle of a scratch VM\n"
            "  python scripts/validate-proxmox.py ... --allow-writes --vmid 901\n\n"
            "see docs/real-hardware-validation.md\n"
        ),
    )
    p.add_argument("--host", required=True, help="Proxmox host / IP")
    p.add_argument("--port", type=int, default=8006)
    p.add_argument("--scheme", choices=("https", "http"), default="https",
                   help="http is for the dev mock ONLY (default: https)")
    p.add_argument("--node", default="", help="node name (HLIDSKJALF_PVE_NODE). "
                                              "Auto-detected from /nodes if omitted.")
    p.add_argument("--token-id", default="hlidskjalf@pve!panel",
                   help="full token id, e.g. 'hlidskjalf@pve!panel'")
    p.add_argument("--token-secret", default="",
                   help="NOT recommended (shell history). Prefer --token-secret-file, the "
                        "HLIDSKJALF_PVE_TOKEN_SECRET env var, or the interactive prompt.")
    p.add_argument("--token-secret-file", default="", help="file containing only the secret")
    p.add_argument("--fingerprint", default="",
                   help="SHA-256 cert fingerprint (HLIDSKJALF_PVE_FINGERPRINT). "
                        "Required for https — pve.py refuses to connect without it.")
    p.add_argument("--vmid", type=int, default=None,
                   help="guest to probe. Read-only unless --allow-writes, in which case it "
                        "MUST be a scratch guest with vmid >= 900.")
    p.add_argument("--clone-storage", default="local-lvm",
                   help="HLIDSKJALF_CLONE_STORAGE; checked for existence (default: local-lvm)")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--no-console", action="store_true",
                   help="skip the console checks (they POST vncproxy, the only non-GET the "
                        "read-only path makes; it changes no guest state)")
    p.add_argument("--allow-writes", action="store_true",
                   help="DANGER: permit ONE start+stop power-cycle of --vmid (which must be "
                        ">= 900). Nothing else is ever mutated.")
    return p


async def run(args, rep: Report) -> int:
    pve = Pve(args, rep)
    try:
        await check_tls(pve, rep)
        await check_auth(pve, rep)
        if any(r.verdict == FAIL and r.check == "auth/token" for r in rep.results):
            return 2

        nodes = await check_nodes(pve, rep)
        if not args.node:
            if not nodes:
                return 2
            args.node = pve.a.node = nodes[0]
            rep.add(INFO, "nodes/auto", f"--node not given; using '{args.node}'")

        resources = await check_cluster_resources(pve, rep, nodes)
        guest = pick_guest(resources, args.vmid)
        running = guest if guest and guest.get("status") == "running" else None

        await check_node_status(pve, rep)
        await check_rrd(pve, rep, guest)
        await check_guest_detail(pve, rep, running or guest)
        await check_tasks_and_upid(pve, rep)
        await check_console(pve, rep, running)

        if args.allow_writes:
            await check_writes(pve, rep)
    finally:
        await pve.aclose()

    rule("Summary")
    c = rep.counts()
    print(f"      {c[PASS]} pass   {c[FAIL]} FAIL   {c[WARN]} warn   {c[SKIP]} skip")
    if c[FAIL]:
        print("\n      Failures (each names the file it breaks):")
        for r in rep.results:
            if r.verdict == FAIL:
                print(f"        - {r.check}: {r.detail}")
    if args.scheme == "http":
        print("\n      NOTE: --scheme http. If this was the dev mock, a clean run proves only that\n"
              "      THIS SCRIPT works. It says nothing whatsoever about real Proxmox.")
    return 1 if c[FAIL] else 0


def main() -> int:
    args = build_parser().parse_args()

    if args.allow_writes:
        if args.vmid is None:
            sys.exit("--allow-writes requires --vmid <scratch vmid>. Refusing to guess.")
        if args.vmid < 900:
            sys.exit(f"--allow-writes refuses vmid {args.vmid}: use a SCRATCH guest with "
                     "vmid >= 900. Never a real one.")

    args.token_secret = resolve_secret(args)
    if not args.token_secret:
        sys.exit("empty token secret")

    rep = Report(secret=args.token_secret)
    print("Hlidskjalf — real-hardware validation")
    print(f"  target : {args.scheme}://{args.host}:{args.port}/api2/json")
    print(f"  token  : {args.token_id} (secret: {len(args.token_secret)} chars, never printed)")
    print(f"  node   : {args.node or '(auto-detect)'}")
    print(f"  mode   : {'WRITES ENABLED on vmid ' + str(args.vmid) if args.allow_writes else 'READ-ONLY'}")

    try:
        return asyncio.run(run(args, rep))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
