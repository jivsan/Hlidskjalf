# NixOS module: services.hlidskjalf
#
# The panel ships unconfigured on purpose: declare nothing but `enable`, open it in
# a browser, and finish in the setup wizard (Proxmox connection -> admin account).
# Everything below is optional, for people who would rather keep configuration in
# their system config than in the panel's database.
#
# Environment always wins over anything the wizard stored (see config.apply_stored),
# so a declared value cannot be edited away in the UI — but an option left empty is
# NOT a declaration, and the panel falls back to what the wizard saved.
flake: { config, lib, pkgs, ... }:

let
  cfg = config.services.hlidskjalf;

  # THE ENVIRONMENT WINS over anything the setup wizard saved — that is deliberate
  # (an ops-managed deploy must not be editable out from under itself). It also
  # means this module must emit NOTHING it was not explicitly told, or its own
  # defaults silently overrule the operator.
  #
  # That is not hypothetical: this module used to default pveNode = "pve" and emit
  # it always. A wizard configured with node "hella" was overridden on every
  # request, and Proxmox answered by trying to PROXY to a node called "pve" and
  # failing DNS — so every node-scoped page showed "hostname lookup 'pve' failed"
  # and nothing pointed at the cause. Hence: null means "not set", and not-set
  # means "not emitted".
  nonEmpty = lib.filterAttrs (_: v: v != null && v != "" && v != "{}" && v != "[]");

  optionalStr = v: if v == null then null else toString v;

  settingsEnv = nonEmpty {
    # Panel-level: owned by the deployment, not by the wizard.
    HLIDSKJALF_HOST = cfg.bindAddress;
    HLIDSKJALF_PORT = toString cfg.port;
    HLIDSKJALF_STATE_DIR = "/var/lib/hlidskjalf";
    HLIDSKJALF_COOKIE_SECURE = lib.boolToString cfg.cookieSecure;
    HLIDSKJALF_UPDATE_CHECK_ENABLED = lib.boolToString cfg.updateCheckEnabled;
    HLIDSKJALF_DEBUG = if cfg.debug then "true" else null;
    HLIDSKJALF_LOG_LEVEL = cfg.logLevel;
    HLIDSKJALF_TRUSTED_PROXIES = lib.concatStringsSep "," cfg.trustedProxies;
    HLIDSKJALF_ADMIN_NETWORKS = lib.concatStringsSep "," cfg.adminNetworks;
    HLIDSKJALF_PUBLIC = lib.boolToString cfg.public;
    HLIDSKJALF_CLOUDFLARE = lib.boolToString cfg.cloudflare;

    # The Proxmox connection: null unless declared, so the wizard owns it.
    HLIDSKJALF_PVE_HOST = cfg.settings.pveHost;
    HLIDSKJALF_PVE_PORT = optionalStr cfg.settings.pvePort;
    HLIDSKJALF_PVE_NODE = cfg.settings.pveNode;
    HLIDSKJALF_PVE_TOKEN_ID = cfg.settings.pveTokenId;
    HLIDSKJALF_PVE_FINGERPRINT = cfg.settings.pveFingerprint;
    HLIDSKJALF_PVE_TLS = cfg.settings.pveTls;

    HLIDSKJALF_PROTECTED_VMIDS =
      lib.concatStringsSep "," (map toString cfg.settings.protectedVmids);
    HLIDSKJALF_BANDWIDTH_QUOTAS = builtins.toJSON cfg.settings.bandwidthQuotas;
    HLIDSKJALF_DEFAULT_SSH_KEYS = cfg.settings.defaultSshKeys;
    HLIDSKJALF_VLAN_GATEWAYS = builtins.toJSON cfg.settings.vlanGateways;
    HLIDSKJALF_CLONE_STORAGE = cfg.settings.cloneStorage;
    HLIDSKJALF_PVE_BRIDGE = cfg.settings.pveBridge;
    HLIDSKJALF_RESCUE_ISO = cfg.settings.rescueIso;
    HLIDSKJALF_ADMIN_USER = cfg.settings.adminUser;
  };
in
{
  options.services.hlidskjalf = {
    enable = lib.mkEnableOption "Hlidskjalf Proxmox panel";

    package = lib.mkOption {
      type = lib.types.package;
      default = flake.packages.${pkgs.system}.hlidskjalf;
      description = "Hlidskjalf package to run.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8787;
      description = "Port the panel listens on.";
    };

    bindAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      example = "0.0.0.0";
      description = ''
        Address to bind. The default assumes a reverse proxy on the same host.
        Set to "0.0.0.0" to reach the panel directly from the LAN — and then read
        `cookieSecure`, or you will not be able to log in.
      '';
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open `port` in the firewall. Never expose this to the internet.";
    };

    cookieSecure = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Mark the session cookie Secure (HTTPS only). This is right behind a TLS
        reverse proxy, and **wrong over plain http**: the browser will not send a
        Secure cookie back over http, so every login appears to succeed and then
        bounces straight back to the login page. Set false ONLY on a trusted LAN
        with no TLS in front.
      '';
    };

    updateCheckEnabled = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Let the panel check GitHub for newer commits (anonymous GET of a public
        repo, fail-soft). It can only ever *report* on a Nix install: applying an
        update means updating this flake input and running nixos-rebuild, so the
        panel shows that command instead of an apply button.
      '';
    };

    trustedProxies = lib.mkOption {
      type = with lib.types; listOf str;
      default = [ ];
      example = [ "127.0.0.1/32" ];
      description = ''
        Reverse proxies whose forwarded headers (X-Forwarded-For, CF-Connecting-IP)
        may be believed. Behind Traefik or cloudflared on the same host this is
        `[ "127.0.0.1/32" ]`.

        Empty (default) = no proxy: the socket peer IS the client and forwarded
        headers are ignored entirely. Get this wrong in the permissive direction and
        anyone can claim any address; get it wrong in the other and every request is
        attributed to the proxy, which makes the audit log useless and the per-IP
        login limiter one shared bucket.
      '';
    };

    adminNetworks = lib.mkOption {
      type = with lib.types; listOf str;
      default = [ ];
      example = [ "100.64.0.0/10" "192.168.1.0/24" ];
      description = ''
        Networks from which admin is permitted. **Empty (default) = anywhere**, which
        is right for a LAN-only panel.

        Set it when the panel is reachable from the internet (a Cloudflare tunnel, a
        port forward): tenants can then sign in from anywhere and manage their one VM,
        while admin exists ONLY inside these networks. Enforced server-side at login,
        at session use, and on every admin route — an admin session that leaves the
        network stops working, because a session cookie travels with the browser.

        `100.64.0.0/10` is the Tailscale range; a tailnet is a better admin boundary
        than a LAN, since it follows you and does not trust every device at home.
      '';
    };

    public = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Declare that this panel is reachable from the internet. It changes nothing on
        its own — `trustedProxies` and `adminNetworks` do the actual work — but with it
        set the panel **refuses to start** unless BOTH of those are configured.

        This is the interlock that makes an unsafe exposure impossible to deploy by
        accident: without `adminNetworks` an internet-facing panel accepts admin login
        from anywhere, and without `trustedProxies` it cannot tell tenants apart from
        the proxy. Turn this on the moment you put a tunnel or port-forward in front of
        the panel; leave it off for a LAN-only deployment.
      '';
    };

    cloudflare = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Set this ONLY if the trusted proxy in front of the panel is Cloudflare.
        Cloudflare overwrites the `CF-Connecting-IP` header at its edge, so it can be
        believed. Any other proxy — Traefik, nginx, Newt/Pangolin, a non-Cloudflare
        cloudflared tunnel — forwards a client-supplied `CF-Connecting-IP` unchanged,
        so trusting it would let anyone spoof their source address and, with it, the
        `adminNetworks` boundary and the per-IP login limiter.

        Off (default): `CF-Connecting-IP` is ignored and only the `X-Forwarded-For`
        chain (walked right-to-left past `trustedProxies`) is believed.
      '';
    };

    debug = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Keep the in-memory log and error buffers that the admin Debug page reads.
        Without this (or logLevel = "DEBUG") that page is simply empty — the
        buffers are never attached. Off by default: it keeps recent requests and
        tracebacks in memory.
      '';
    };

    logLevel = lib.mkOption {
      type = with lib.types; nullOr (enum [ "DEBUG" "INFO" "WARNING" "ERROR" ]);
      default = null;
      description = "Log level. Unset = the panel's own default (INFO).";
    };

    environmentFile = lib.mkOption {
      type = with lib.types; nullOr path;
      default = null;
      example = "/run/secrets/hlidskjalf.env";
      description = ''
        Optional root-owned 0600 env file for secrets — HLIDSKJALF_PVE_TOKEN_SECRET,
        HLIDSKJALF_ADMIN_PASSWORD_HASH, HLIDSKJALF_SESSION_SECRET. Each also takes a
        `*_FILE` twin, for agenix/sops/systemd-creds.

        Leave it null to configure through the **setup wizard** instead: the panel
        stores the token encrypted at rest and generates its own session key.
      '';
    };

    settings = {
      # Every one of these is null by default, meaning "the wizard owns it".
      # Declare one only to take it away from the UI — the environment wins, and a
      # value set here CANNOT be changed in Settings.
      pveHost = lib.mkOption {
        type = with lib.types; nullOr str;
        default = null;
        example = "192.0.2.10";
        description = "Proxmox host or IP. Null = ask in the setup wizard.";
      };
      pvePort = lib.mkOption {
        type = with lib.types; nullOr port;
        default = null;
        description = "Null = the panel's default, 8006.";
      };
      pveNode = lib.mkOption {
        type = with lib.types; nullOr str;
        default = null;
        example = "pve";
        description = ''
          The node name Proxmox itself reports (the one in its web UI tree).

          Null = whatever the wizard was told, which is almost always what you want.
          Setting it here OVERRIDES the wizard: if it does not match the real node
          name, every node-scoped page fails, because Proxmox tries to proxy the
          request to a host by that name and cannot resolve it.
        '';
      };
      pveTokenId = lib.mkOption {
        type = with lib.types; nullOr str;
        default = null;
        example = "hlidskjalf@pve!panel";
        description = "Null = ask in the setup wizard.";
      };
      pveFingerprint = lib.mkOption {
        type = with lib.types; nullOr str;
        default = null;
        description = ''
          SHA-256 fingerprint of the Proxmox API TLS cert, colon-separated hex:
          `openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256`
          — but note pveproxy serves `pveproxy-ssl.pem` instead when a custom
          certificate is installed, so read the one actually being served.

          Null = ask in the wizard. Ignored when pveTls = "system".
        '';
      };
      pveTls = lib.mkOption {
        type = with lib.types; nullOr (enum [ "pin" "system" ]);
        default = null;
        description = ''
          How https is verified. "pin" (default) accepts exactly one certificate,
          by SHA-256 fingerprint — correct for the self-signed cert Proxmox ships.
          "system" does ordinary CA-chain + hostname verification — correct when
          Proxmox serves an ACME/Let's Encrypt certificate, whose fingerprint
          changes on every renewal and would take a pinned panel offline with it.
        '';
      };

      protectedVmids = lib.mkOption {
        type = with lib.types; listOf int;
        default = [ ];
        example = [ 100 ];
        description = ''
          Destroy/reinstall/stop/reset are refused server-side for these guests.
          Empty means NOTHING is protected — including the guest this panel runs on,
          which an admin could then destroy from inside the panel. Put its VMID here.
        '';
      };
      bandwidthQuotas = lib.mkOption {
        type = with lib.types; attrsOf int;
        default = { };
        example = { "115" = 500; };
        description = "vmid -> GB per month, display-only.";
      };
      defaultSshKeys = lib.mkOption { type = lib.types.lines; default = ""; };
      vlanGateways = lib.mkOption {
        type = with lib.types; attrsOf str;
        default = { };
        example = { "20" = "192.0.2.1"; "30" = ""; };
        description = ''
          VLAN tag -> gateway IP ("" for gateway-less VLANs). Also editable in
          Settings -> provisioning; leave empty here to manage it there.
        '';
      };
      cloneStorage = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "local-lvm";
        description = ''
          Storage new VM disks are cloned onto. `local-lvm` is only Proxmox's usual
          default, not a guarantee — if it does not exist on your node, every
          provision fails. Empty = manage it in Settings, which lists what the node
          actually reports.
        '';
      };
      pveBridge = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "vmbr0";
        description = "Bridge new NICs attach to. Empty = manage it in Settings.";
      };
      rescueIso = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "local:iso/systemrescue-12.01-amd64.iso";
      };
      adminUser = lib.mkOption {
        type = with lib.types; nullOr str;
        default = null;
        example = "admin";
        description = ''
          Username of the bootstrap admin. Null = the wizard asks. Declaring it
          here overrides what the wizard saved, so leave it null unless the account
          is created from `environmentFile` (HLIDSKJALF_ADMIN_PASSWORD_HASH).
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {
    warnings = lib.optional (cfg.trustedProxies != [ ] && cfg.adminNetworks == [ ]) ''
      services.hlidskjalf declares trustedProxies but no adminNetworks. If this panel is
      reachable from the internet, ADMIN IS TOO — anyone who guesses an admin password
      gets your whole fleet. Set adminNetworks (e.g. your tailnet) so the public side is
      tenants-only.
    '' ++ lib.optional (cfg.settings.protectedVmids == [ ]) ''
      services.hlidskjalf.settings.protectedVmids is empty: NOTHING is protected from
      destroy/reinstall, including the machine this panel runs on. Set it to the VMID
      of this guest (and anything else precious) before giving anyone an admin login.
    '' ++ lib.optional (cfg.bindAddress != "127.0.0.1" && cfg.cookieSecure) ''
      services.hlidskjalf binds ${cfg.bindAddress} with cookieSecure = true. If you
      reach it over plain http, the browser will not return the session cookie and
      login will appear to fail silently. Put TLS in front, or set cookieSecure = false.
    '';

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];

    systemd.services.hlidskjalf = {
      description = "Hlidskjalf Proxmox panel";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = settingsEnv;
      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        DynamicUser = true;
        StateDirectory = "hlidskjalf";
        Restart = "on-failure";
        RestartSec = 5;

        # hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
        RestrictNamespaces = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [ "@system-service" "~@privileged" ];
        CapabilityBoundingSet = "";
        AmbientCapabilities = "";
      };
    };
  };
}
