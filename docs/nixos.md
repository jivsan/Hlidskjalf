# Deploying Hlidskjalf on NixOS

The panel ships as a flake with a NixOS module. Declare `enable` and nothing else, and
you get a service on `127.0.0.1:8787` that serves the **setup wizard** — point it at your
Proxmox from the browser. Everything else in `services.hlidskjalf.settings` is optional,
for people who would rather keep configuration in their system config than in the panel's
database.

**Environment always wins** over anything the wizard stored, so a value declared here
cannot be edited away in the UI. An option left empty is *not* a declaration — the panel
falls back to whatever the wizard saved.

---

## 1. Add the flake input

```nix
# flake.nix
inputs.hlidskjalf = {
  url = "github:jivsan/Hlidskjalf";
  inputs.nixpkgs.follows = "nixpkgs";   # one nixpkgs, not two
};
```

Pass `inputs` through to your host modules (`specialArgs = { inherit inputs; }` in
`nixosSystem`, which most configurations already do).

## 2. Import the module and turn it on

```nix
{ inputs, ... }:
{
  imports = [ inputs.hlidskjalf.nixosModules.hlidskjalf ];

  services.hlidskjalf = {
    enable = true;

    # Reverse proxy in front (recommended): keep the panel on loopback and let the
    # proxy terminate TLS. These are the defaults; shown here because they matter.
    bindAddress = "127.0.0.1";
    port = 8787;
    cookieSecure = true;

    settings = {
      # The VMID of THIS guest, plus anything else that must never be destroyed.
      # Empty means nothing is protected — including the machine you are reading this on.
      protectedVmids = [ 100 ];
    };
  };
}
```

Then `nixos-rebuild switch`, open the panel, and finish in the wizard: Proxmox host, node
name, token id, token secret, cert fingerprint (see the README for making a scoped token
and reading the fingerprint), then your admin account.

That is the whole install. The token is encrypted at rest under `/var/lib/hlidskjalf`,
and the service runs as a `DynamicUser` with a locked-down systemd sandbox.

## 3. Behind a reverse proxy

The panel is a single HTTP service — one port, no static assets to route separately. The
only thing to get right is **WebSockets**: the console (`/ws/...`) will not work if the
proxy drops `Upgrade`. Traefik, nginx (`proxy_set_header Upgrade $http_upgrade`) and
Caddy all handle this correctly by default or with their usual websocket stanza.

Traefik, as a dynamic-config router and service:

```nix
http.routers.hlidskjalf = {
  rule = "Host(`panel.example.org`)";
  entryPoints = [ "websecure" ];
  service = "hlidskjalf";
  tls = { };
};

http.services.hlidskjalf.loadBalancer.servers = [
  { url = "http://127.0.0.1:8787"; }
];
```

> **The cookie trap.** The session cookie is `Secure`, so the browser only returns it over
> HTTPS. Behind TLS this is correct and invisible. Hit the panel directly over plain
> `http://host:8787` and every login will *appear* to succeed and then bounce you back to
> the login page — the cookie was never sent back. If you genuinely have no TLS in front,
> set `cookieSecure = false`, and only on a trusted LAN.

To reach it without a proxy at all:

```nix
services.hlidskjalf = {
  bindAddress = "0.0.0.0";
  openFirewall = true;
  cookieSecure = false;    # no TLS => the cookie must not be Secure
};
```

**Never expose the panel to the internet.** It holds a token that can destroy VMs.

## 4. The Debug page, and log level

The admin **Debug** page reads in-memory log and error buffers that are only attached when
the panel runs with debug on. Without it the page is permanently empty, which looks like a
bug and isn't:

```nix
services.hlidskjalf.debug = true;      # or: logLevel = "DEBUG";
```

## 5. Declaring config instead of using the wizard

Every setting has an option, and every secret takes a `*_FILE` twin so it can come from
agenix / sops-nix / systemd-creds:

```nix
services.hlidskjalf = {
  enable = true;
  environmentFile = config.age.secrets.hlidskjalf.path;   # HLIDSKJALF_PVE_TOKEN_SECRET=...

  settings = {
    pveHost = "192.0.2.10";
    pveNode = "pve";                    # the name Proxmox itself reports
    pveTokenId = "hlidskjalf@pve!panel";
    pveFingerprint = "AA:BB:…:FF";      # openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256
    protectedVmids = [ 100 101 ];
    cloneStorage = "local-lvm";         # only Proxmox's usual default — check yours
    pveBridge = "vmbr0";                # often not vmbr0
    vlanGateways = { "20" = "192.0.2.1"; };
  };
};
```

**Every option here defaults to `null`, meaning "the wizard owns it".** Declare one only
to take it away from the UI — the environment always wins, so a value set here *cannot* be
changed in Settings, and a wrong one silently overrides what the wizard was told. (The
module used to default `pveNode = "pve"` and emit it always; a panel configured for a node
named something else then failed every node-scoped page with a DNS error. Hence `null`.)

Leave `cloneStorage`, `pveBridge` and `vlanGateways` unset to manage them in
**Settings → provisioning**, and `pveHost` / `pveNode` / `pveTokenId` / `pveFingerprint`
unset to manage the connection in **Settings → Proxmox** — where the panel offers what your
node actually reports rather than what you remembered.

### Certificates that renew

`pveTls = "system"` verifies the CA chain and hostname like a browser, instead of pinning
one certificate. Use it when Proxmox serves an ACME/Let's Encrypt certificate: a pin dies
on every renewal (~60 days), taking the panel's Proxmox connection with it.

## 6. Updating

A Nix deployment updates the way the rest of your system does:

```bash
nix flake update hlidskjalf     # in your config repo
nixos-rebuild switch
```

**Settings → Updates still tells you when a new commit lands** — it compares the running
version against the tip of the branch on GitHub and shows the commits — but the *apply*
button stays disabled on Nix, deliberately. A NixOS system's source of truth is its flake;
a service that rewrites its own store path is lying to the thing that manages it. The
panel shows the command instead. (`POST /api/update` refuses on Nix regardless of what a
client sends.)

Disable the check entirely with `updateCheckEnabled = false`.

## 7. When the build fails

- **`npmDepsHash` mismatch** — expected after any `frontend/package-lock.json` change.
  The error prints the correct hash (`got: sha256-…`); put it in `nix/package.nix`. Or
  compute it up front: `nix run nixpkgs#prefetch-npm-deps -- frontend/package-lock.json`.
- **`ModuleNotFoundError` at runtime** — a dependency exists in `backend/pyproject.toml`
  but not in `nix/package.nix`. Nix gives the app *only* what that list names. The build
  now runs `pythonImportsCheck`, and `backend/tests/test_nix_package.py` fails the CI
  suite on that drift, so it should not reach you — but that is what it means.
