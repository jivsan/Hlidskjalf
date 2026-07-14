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

  # Only pass through what the operator actually set. An empty env var is not a
  # configuration choice, and shipping HLIDSKJALF_PVE_HOST="" would be noise.
  nonEmpty = lib.filterAttrs (_: v: v != null && v != "" && v != "{}" && v != "[]");

  settingsEnv = nonEmpty {
    HLIDSKJALF_HOST = cfg.bindAddress;
    HLIDSKJALF_PORT = toString cfg.port;
    HLIDSKJALF_STATE_DIR = "/var/lib/hlidskjalf";
    HLIDSKJALF_COOKIE_SECURE = lib.boolToString cfg.cookieSecure;
    HLIDSKJALF_UPDATE_CHECK_ENABLED = lib.boolToString cfg.updateCheckEnabled;

    HLIDSKJALF_PVE_HOST = cfg.settings.pveHost;
    HLIDSKJALF_PVE_PORT = toString cfg.settings.pvePort;
    HLIDSKJALF_PVE_NODE = cfg.settings.pveNode;
    HLIDSKJALF_PVE_TOKEN_ID = cfg.settings.pveTokenId;
    HLIDSKJALF_PVE_FINGERPRINT = cfg.settings.pveFingerprint;

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
      pveHost = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "192.0.2.10";
        description = "Proxmox host or IP. Empty = ask in the setup wizard.";
      };
      pvePort = lib.mkOption { type = lib.types.port; default = 8006; };
      pveNode = lib.mkOption {
        type = lib.types.str;
        default = "pve";
        description = ''
          The node name Proxmox itself reports (the one in its web UI tree). Every
          node-scoped page 404s if this is wrong.
        '';
      };
      pveTokenId = lib.mkOption { type = lib.types.str; default = "hlidskjalf@pve!panel"; };
      pveFingerprint = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = ''
          SHA-256 fingerprint of the Proxmox API TLS cert, colon-separated hex:
          `openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256`.
          Empty = ask in the wizard. There is no unpinned https: the panel refuses
          to connect over https without a pin.
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
        type = lib.types.str;
        default = "admin";
        description = "Username of the bootstrap admin. A role, not a person.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    warnings = lib.optional (cfg.settings.protectedVmids == [ ]) ''
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
