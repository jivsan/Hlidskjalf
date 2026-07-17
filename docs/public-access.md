# Exposing the panel: tenants from anywhere, admin from nowhere

The default posture is **do not expose this panel**. It holds a token that can destroy
VMs, and an internet-facing login page is a standing invitation.

But the VPS model wants the opposite: you provision a VM for a friend, and they need to
reach it — console, power, bandwidth — without being on your LAN. So the panel supports
exactly one shape of exposure, and it is not "put it on the internet and hope":

> **Tenants can sign in from anywhere. Admin exists only inside `admin_networks`.**

Enforced server-side, in three places, because a URL only tenants know about is not a
boundary — it is a wish:

1. An admin **cannot log in** from outside the admin networks.
2. An admin **session** that arrives from outside is refused — even the console
   websocket. A cookie travels with the browser: sign in at home, open the same laptop
   on café wifi, and the cookie is still valid. The check lives at the session layer,
   so every authenticated path passes through it exactly once.
3. Admin routes 403 from outside regardless.

A tenant, meanwhile, sees exactly one VM — which the panel already enforces on every
route (`_ensure_vm_access`).

---

## 1. Teach the panel who is calling

Behind a reverse proxy, every request arrives *from the proxy*. Attribute requests to
the socket peer and the audit log records `127.0.0.1` for everything anyone ever does,
and the per-IP login limiter becomes one shared bucket that a single attacker fills on
everybody's behalf.

```nix
services.hlidskjalf.trustedProxies = [ "127.0.0.1/32" ];   # traefik / cloudflared, same host
```

Forwarded headers (`X-Forwarded-For`, `CF-Connecting-IP`) are believed **only** when the
socket peer is one of these. Anyone can *send* `X-Forwarded-For: 100.64.0.1`; only a
proxy you named may speak for someone else. With no trusted proxy declared, the headers
are ignored entirely.

## 2. Decide where admin lives

```nix
services.hlidskjalf.adminNetworks = [ "100.64.0.0/10" ];   # your tailnet
```

**A tailnet is a better admin boundary than a LAN.** It follows you (you can administer
from anywhere you are logged into Tailscale), and it does not trust every device that
happens to be on your home wifi. `100.64.0.0/10` is Tailscale's range.

Empty (the default) means *anywhere*, which is correct for a LAN-only panel — and the
module warns if you declare `trustedProxies` without `adminNetworks`, because that is
the combination that puts admin on the internet.

## 3. Put a tunnel in front

A Cloudflare tunnel needs no port forward, no public IP, and does not expose the origin.
`cloudflared` runs beside the panel and dials *out*.

```nix
# hosts/<host>/modules/system/cloudflared.nix
{ config, ... }:
{
  services.cloudflared = {
    enable = true;
    tunnels."<tunnel-uuid>" = {
      credentialsFile = "/var/lib/cloudflared/<tunnel-uuid>.json";
      ingress."panel.example.com".service = "http://127.0.0.1:8787";
      default = "http_status:404";
    };
  };
}
```

Create the tunnel and its DNS route once, on the host:

```bash
cloudflared tunnel login
cloudflared tunnel create hlidskjalf
cloudflared tunnel route dns hlidskjalf panel.example.com
```

The credentials file is a secret: root-owned, `0600`, and **not** in your config repo.

WebSockets pass through Cloudflare, so the tenant console works. Nothing else on the
host is exposed — the tunnel maps one hostname to one local port.

Keep your existing internal route (Traefik, `lan-only`) for admin. Same panel, two
doors, and only one of them is on the internet.

### Alternative: a self-hosted tunnel (Pangolin + Newt)

Cloudflare is not the only shape. A **self-hosted** tunnel — e.g. [Pangolin](https://pangolin.net)
fronting a `newt` agent that dials out from beside the panel — works the same way from the
panel's point of view: the agent forwards from `127.0.0.1`, and Pangolin's reverse proxy
sets `X-Forwarded-For`, so `trustedProxies = [ "127.0.0.1/32" ]` and `adminNetworks` stay
exactly as above. You own the edge instead of renting it, and — unlike Cloudflare's proxy,
which carries HTTP/HTTPS only — such a tunnel can also forward **raw TCP/UDP** (SSH, VNC),
which matters if you want to give tenants a direct path into their VM, not just the panel.

### Do you need a separate host for the tunnel?

No — `cloudflared` dials *out*, so there is no inbound path to host and nothing to port
forward. It runs fine beside the panel.

The argument for a dedicated tunnel host (an LXC is plenty; a VM is heavier than this
needs) is **blast radius**, not connectivity: it is the one internet-facing daemon you
run, and putting it on the box that also holds your photos, documents and dashboards
means a compromise of it lands in the middle of all of them. On its own VLAN, with a
firewall rule permitting only the panel's port, whoever gets in is in a throwaway
machine that can talk to exactly one thing.

Weigh it honestly: the realistic threat to an exposed panel is someone guessing a
password, not a 0-day in a well-audited Go binary making outbound connections — and a
separate tunnel host does nothing about that. It is defence in depth, not the defence.

**If you do add the hop, declare BOTH proxies:**

```nix
services.hlidskjalf.trustedProxies = [ "127.0.0.1/32" "192.0.2.5/32" ];  # traefik + tunnel host
```

The panel walks the `X-Forwarded-For` chain from the right, skipping addresses it knows
are yours, and takes the first one that is not. Declare only the local proxy and every
tenant on earth is recorded as coming from your tunnel host.

### `CF-Connecting-IP` is only believed behind Cloudflare

`X-Forwarded-For` is a chain the panel can walk, so a client-prepended entry loses to the
real one your proxy appends. `CF-Connecting-IP` is a *single* value with no chain — only
Cloudflare's edge overwrites it. Any other proxy (Traefik, nginx, Newt/Pangolin) forwards
whatever the client sent, so believing it would let anyone name their own source address
and step straight into `admin_networks`.

So the panel ignores `CF-Connecting-IP` entirely unless you opt in:

```nix
services.hlidskjalf.cloudflare = true;   # ONLY if Cloudflare is the trusted proxy
```

Off (the default), only the `X-Forwarded-For` walk is trusted. Behind a non-Cloudflare
proxy leave it off — and, belt and braces, configure that proxy to strip any inbound
`CF-Connecting-IP` (and client-supplied `X-Forwarded-For`).

## 4. What an internet-facing login page gets you

- **Per-IP rate limiting** — the right defence against one abusive host.
- **Per-account backoff** — 10 failures in 15 minutes and the account stops answering,
  wherever the guesses come from. Per-IP limiting does nothing about a botnet spreading
  attempts across thousands of addresses, which is exactly what a public login attracts.
  It expires: a permanent lock is a denial of service any stranger can inflict by typing
  your username wrong ten times.
- **argon2** password hashing, sessions bound to the password epoch (a password change
  invalidates every older session), CSRF on every mutation.
- Cloudflare's own bot/rate rules, if you want them, are free and sit in front of all of
  this.

## 5. What is still true

- The panel is **not** multi-tenant isolation in the hostile sense. A tenant reaches one
  VM through a panel that holds a token which can reach *all* of them. The authorisation
  is real and tested — but the blast radius of a panel-level RCE is the whole fleet, and
  exposing it puts that in front of the internet. That is the trade you are making.
- Give tenants **their own accounts**, never share one.
- Keep `protected_vmids` set, including the guest the panel itself runs on.

## 6. The interlock: `public` makes the safe config mandatory

Everything above is only a defence if it was actually configured. The failure this
exists to prevent is a panel put on the internet with `admin_networks` empty (admin
login from anywhere) or `trusted_proxies` empty (blind to the caller) — each one unset
env var away.

```nix
services.hlidskjalf.public = true;
```

`public` changes no behaviour by itself. It is a declaration — *this panel is exposed* —
and with it set the panel **refuses to start** unless both `adminNetworks` and
`trustedProxies` are configured, naming whichever is missing. All three are env-only
(never wizard- or Settings-writable), so the check is final at load: an unsafe exposure
cannot be deployed by accident. Turn it on the moment a tunnel or port-forward goes in
front of the panel; leave it off for a LAN-only deployment (the default), which stays
unconstrained.

## 7. Phishing and credential attacks — what the panel does about them

The login page is the one page the whole internet sees, so it gets the paranoid
reading:

- **Clickjacking is closed by construction.** Every response carries
  `frame-ancestors 'none'` (and `X-Frame-Options: DENY` for older browsers), so a
  lookalike site cannot frame the real login and skim credentials. A strict
  single-origin CSP (`default-src 'self'`, no remote scripts) plus React's escaping
  covers injection; these headers are pinned by tests so they cannot silently
  regress. The panel never loads third-party assets, so a compromised CDN has no
  reach here either.
- **The panel never sends email or messages.** There is nothing to spoof: any
  "Hlidskjalf" mail, DM, or password reset request is fake by definition. Password
  changes happen only in the panel, behind the current password.
- **Guessing is expensive and confirmation-free.** Failed logins are
  indistinguishable (a valid admin password from outside `admin_networks` gets the
  same generic 401 as a wrong one), per-IP attempts are limited, and per-account
  failures back off with a cap that cannot be used as a memory bomb. An attacker
  gets ~36 unverifiable guesses an hour against a real password.
- **Sessions are bound and revocable.** Cookies are `SameSite=Strict` + HttpOnly,
  bound to the password epoch (a password change kills every older session), and
  the session secret is encrypted at rest and redacted from logs.
- **Admin exists only on your networks.** Even a perfectly phished admin password
  is worthless from the internet — the login only works from inside
  `admin_networks`, and a session that wanders out stops working.

The residual human layer is out of the panel's reach: tenants should reach the
panel only through the address you give them, and admin work stays on the tailnet.
