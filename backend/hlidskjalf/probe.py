"""The Proxmox connection, and proving it works before anything is persisted.

Shared by the first-run wizard (`routes/setup.py`) and the admin connection editor
(`routes/settings.py`) so both validate a connection the *same* way: talk to the
real Proxmox, confirm the node exists, and only then let the caller save.

A half-configured panel that cannot reach Proxmox is worse than one that is still
unconfigured — it looks installed and does nothing.
"""

import ipaddress

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .pve import PveClient, PveError

# How the panel decides the Proxmox it is talking to is really the Proxmox it
# means to talk to. There is no third option, and no "just trust it" mode.
TLS_PIN = "pin"        # SHA-256 fingerprint of one exact certificate (self-signed)
TLS_SYSTEM = "system"  # normal CA chain + hostname verification (ACME / internal CA)


class PveConn(BaseModel):
    host: str = Field(min_length=1)
    port: int = 8006
    node: str = Field(min_length=1)
    scheme: str = "https"
    token_id: str = Field(min_length=1)
    token_secret: str = Field(min_length=1)
    fingerprint: str = ""
    tls: str = TLS_PIN


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_tls_choice(conn: PveConn) -> None:
    """Reject the TLS combinations that cannot possibly verify anything.

    Raises HTTPException(400) with an explanation the operator can act on, rather
    than letting `PveClient` raise an opaque RuntimeError at startup.
    """
    if conn.scheme != "https":
        return
    if conn.tls == TLS_SYSTEM:
        if is_ip_literal(conn.host):
            raise HTTPException(
                400,
                "System-CA verification checks the certificate's hostname, and a "
                f"CA-issued certificate is issued to a name — not to {conn.host}. "
                "Use the hostname the certificate was issued for, or switch to "
                "fingerprint pinning.",
            )
        return
    if not conn.fingerprint:
        raise HTTPException(
            400,
            "An https connection needs either the Proxmox certificate fingerprint "
            "(`openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256` "
            "on the host — note pveproxy serves pveproxy-ssl.pem instead when a custom "
            "certificate is installed), or system-CA verification if the certificate "
            "was issued by a real CA.",
        )


def probe_settings(conn: PveConn) -> Settings:
    """A throwaway Settings carrying only the connection under test."""
    s = get_settings().model_copy()
    for field, value in {
        "pve_host": conn.host,
        "pve_port": conn.port,
        "pve_node": conn.node,
        "pve_scheme": conn.scheme,
        "pve_token_id": conn.token_id,
        "pve_token_secret": conn.token_secret,
        "pve_fingerprint": conn.fingerprint,
        "pve_tls": conn.tls,
    }.items():
        object.__setattr__(s, field, value)
    return s


async def probe(conn: PveConn) -> dict:
    """Talk to Proxmox with the supplied credentials. Raises HTTPException(400)."""
    check_tls_choice(conn)
    client = PveClient(probe_settings(conn))
    try:
        nodes = await client.get("/nodes") or []
        names = [n.get("node") for n in nodes if n.get("node")]
        if conn.node not in names:
            raise HTTPException(
                400,
                f"Connected, but this Proxmox has no node named '{conn.node}'. "
                f"Found: {', '.join(names) or 'none'}.",
            )
        resources = await client.cluster_resources() or []
        # Hand back the actual guests, so the wizard can offer a picker for the
        # first user's VM instead of asking someone to type a VMID from memory.
        guest_list = sorted(
            (
                {"vmid": r["vmid"], "name": r.get("name") or f"vm {r['vmid']}"}
                for r in resources
                if r.get("type") in ("qemu", "lxc") and r.get("vmid") is not None
            ),
            key=lambda g: g["vmid"],
        )
        return {
            "ok": True,
            "node": conn.node,
            "guests": len(guest_list),
            "guest_list": guest_list,
            "nodes": names,
        }
    except HTTPException:
        raise
    except PveError as e:
        # PveError covers BOTH "the host said no" and "we never reached the host"
        # (pve.py wraps transport failures as 502). Reporting a TLS pin mismatch as
        # "Proxmox rejected the credentials" sends people to re-check their token
        # when the certificate is the problem. Split them.
        if e.status == 502:
            raise HTTPException(
                400, f"Could not reach Proxmox at {conn.host}:{conn.port} — {e}"
            )
        if e.status in (401, 403):
            raise HTTPException(
                400,
                f"Proxmox rejected the credentials — check the token id and secret, "
                f"and that the token was created with --privsep 0. ({e})",
            )
        raise HTTPException(400, f"Proxmox refused the request: {e}")
    except Exception as e:  # connection refused, TLS failure, DNS, timeout…
        raise HTTPException(400, f"Could not reach Proxmox at {conn.host}:{conn.port} — {e}")
    finally:
        await client.aclose()
