# NixOS module: services.hlidskjalf
flake: { config, lib, pkgs, ... }:

let
  cfg = config.services.hlidskjalf;
  settingsEnv = {
    HLIDSKJALF_HOST = "127.0.0.1";
    HLIDSKJALF_PORT = toString cfg.port;
    HLIDSKJALF_PVE_HOST = cfg.settings.pveHost;
    HLIDSKJALF_PVE_NODE = cfg.settings.pveNode;
    HLIDSKJALF_PVE_TOKEN_ID = cfg.settings.pveTokenId;
    HLIDSKJALF_PVE_FINGERPRINT = cfg.settings.pveFingerprint;
    HLIDSKJALF_RESCUE_ISO = cfg.settings.rescueIso;
    HLIDSKJALF_PROTECTED_VMIDS =
      lib.concatStringsSep "," (map toString cfg.settings.protectedVmids);
    HLIDSKJALF_BANDWIDTH_QUOTAS = builtins.toJSON cfg.settings.bandwidthQuotas;
    HLIDSKJALF_DEFAULT_SSH_KEYS = cfg.settings.defaultSshKeys;
    HLIDSKJALF_VLAN_GATEWAYS = builtins.toJSON cfg.settings.vlanGateways;
    HLIDSKJALF_CLONE_STORAGE = cfg.settings.cloneStorage;
    HLIDSKJALF_STATE_DIR = "/var/lib/hlidskjalf";
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
      description = "Local port the panel listens on (bind 127.0.0.1, Traefik in front).";
    };

    environmentFile = lib.mkOption {
      type = lib.types.path;
      description = ''
        Root-owned 0600 env file with the secrets:
        HLIDSKJALF_PVE_TOKEN_SECRET, HLIDSKJALF_ADMIN_PASSWORD_HASH,
        HLIDSKJALF_SESSION_SECRET.
      '';
    };

    settings = {
      pveHost = lib.mkOption { type = lib.types.str; default = "10.0.20.10"; };
      pveNode = lib.mkOption { type = lib.types.str; default = "hella"; };
      pveTokenId = lib.mkOption { type = lib.types.str; default = "hlidskjalf@pve!panel"; };
      pveFingerprint = lib.mkOption {
        type = lib.types.str;
        description = "SHA-256 fingerprint of hella's API TLS cert (colon-separated hex).";
      };
      rescueIso = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "local:iso/systemrescue-12.01-amd64.iso";
      };
      protectedVmids = lib.mkOption {
        type = with lib.types; listOf int;
        default = [ 151 ];
        description = "Destroy/reinstall/stop/reset are refused server-side for these.";
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
        default = { "20" = "10.0.20.1"; "30" = ""; "50" = "10.0.50.1"; };
      };
      cloneStorage = lib.mkOption { type = lib.types.str; default = "local-lvm"; };
      adminUser = lib.mkOption { type = lib.types.str; default = "christina"; };
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.hlidskjalf = {
      description = "Hlidskjalf Proxmox panel";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = settingsEnv;
      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        EnvironmentFile = cfg.environmentFile;
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
