"""Where a request actually came from, and whether that place may administer.

Two facts the panel could not previously establish, and both become load-bearing
the moment it is reachable from the internet:

1. **The real client IP.** Behind a reverse proxy every request arrives from the
   proxy — `127.0.0.1` for Traefik on the same host. Attribute requests to that and
   the audit log says "127.0.0.1" for every action ever taken, and the per-IP login
   rate limiter becomes one global bucket that a single attacker can fill on behalf
   of everybody.

   The forwarded headers are only believable when the *socket peer* is a proxy we
   trust. Anyone can send `X-Forwarded-For: 1.2.3.4`; only a trusted proxy's word
   for it counts. So: peer not trusted -> use the peer, ignore the headers.

2. **Whether that IP may hold admin.** The panel is going on the internet so tenants
   can reach their own VM. Admin must not follow it there. This is enforced
   server-side in three places (login, session use, admin routes), because a URL
   that only tenants "know about" is not a boundary — it is a wish.
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

log = logging.getLogger("hlidskjalf.netzone")

# The headers a trusted proxy may use to tell us who it is speaking for, in the
# order we believe them. CF-Connecting-IP is Cloudflare's, and is a single address;
# X-Forwarded-For is a chain, and the LAST entry is the one our trusted proxy added.
_CF_HEADER = "cf-connecting-ip"
_XFF_HEADER = "x-forwarded-for"


def _networks(cidrs: list[str]) -> list[ipaddress._BaseNetwork]:
    out = []
    for raw in cidrs:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            log.warning("ignoring malformed network %r", raw)
    return out


def _ip(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def in_networks(addr: str, cidrs: list[str]) -> bool:
    ip = _ip(addr)
    if ip is None:
        return False
    return any(ip in net for net in _networks(cidrs))


def client_ip(request: Request | None, trusted_proxies: list[str]) -> str:
    """The address the request really came from.

    Forwarded headers are honoured ONLY when the socket peer is a trusted proxy —
    otherwise a client could name any address it liked and walk straight into the
    admin network.
    """
    if request is None or request.client is None:
        return "-"
    peer = request.client.host
    if not trusted_proxies or not in_networks(peer, trusted_proxies):
        return peer  # not a proxy we trust: its own address is the only truth here

    cf = request.headers.get(_CF_HEADER)
    if cf and _ip(cf):
        return cf.strip()

    xff = request.headers.get(_XFF_HEADER)
    if xff:
        # "client, proxy1, proxy2" — walk from the right, skipping proxies we trust,
        # and take the first address that is not one of ours. Taking the leftmost
        # entry instead would let a client prepend a forged address.
        for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
            if _ip(hop) and not in_networks(hop, trusted_proxies):
                return hop
    return peer


def is_admin_zone(request: Request | None, settings) -> bool:
    """May a request from here hold admin?

    An empty `admin_networks` means "anywhere" — the default, and what every
    LAN-only deployment wants. Set it, and admin exists only inside those networks;
    everywhere else the panel is a tenant panel and nothing more.
    """
    if not settings.admin_networks:
        return True
    return in_networks(client_ip(request, settings.trusted_proxies), settings.admin_networks)


def admin_zone_error(settings) -> str:
    return (
        "Administration is restricted to the local network "
        f"({', '.join(settings.admin_networks)}). This panel is reachable publicly so "
        "tenants can manage their own VM; admin is not, by design."
    )
