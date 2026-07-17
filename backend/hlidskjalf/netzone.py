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


def client_ip(request: Request | None, trusted_proxies: list[str], trust_cf: bool = False) -> str:
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

    # CF-Connecting-IP is a single client-settable value with no chain to walk. Only
    # Cloudflare's edge overwrites it, so it may be believed ONLY in cloudflare mode;
    # any other proxy (Traefik, nginx, Newt/Pangolin) forwards whatever the client
    # sent, so trusting it unconditionally lets anyone spoof their source address —
    # and with it the admin-network boundary and the per-IP login limiter.
    if trust_cf:
        cf = request.headers.get(_CF_HEADER)
        if cf and _ip(cf):
            return cf.strip()

    # A header field may arrive as SEVERAL lines (RFC 7230 §3.2.2), and for a
    # list-valued field like XFF the message is semantically ONE comma-list: the
    # lines concatenated in arrival order. getlist() preserves that wire order,
    # and proxies APPEND — a proxy can only add its word after whatever the client
    # already sent, never before it — so the concatenation of every line, in
    # order, is the real chain: client claims on the left, the last proxy's word
    # on the right. Reading only the first line (headers.get) let a client put a
    # spoofed admin-zone address on line 1 with the honest chain on line 2, and
    # be believed.
    #
    # "client, proxy1, proxy2" — walk from the right, skipping proxies we trust,
    # and take the first address that is not one of ours. Taking the leftmost
    # entry instead would let a client prepend a forged address. Because every
    # trusted proxy's append lands to the RIGHT of all client-supplied values,
    # this walk always stops at or before the outermost trusted proxy's word for
    # the real client — a client-supplied entry can never be resolved while a
    # trusted proxy is in front.
    hops = [
        h.strip()
        for line in request.headers.getlist(_XFF_HEADER)
        for h in line.split(",")
        if h.strip()
    ]
    for hop in reversed(hops):
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
    return in_networks(
        client_ip(request, settings.trusted_proxies, settings.cloudflare), settings.admin_networks
    )


def admin_zone_error(settings) -> str:
    return (
        "Administration is restricted to the local network "
        f"({', '.join(settings.admin_networks)}). This panel is reachable publicly so "
        "tenants can manage their own VM; admin is not, by design."
    )
