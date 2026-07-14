"""All configuration from environment variables, prefix HLIDSKJALF_."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HLIDSKJALF_")

    # Proxmox API.
    # No site-specific defaults: every deployment sets these. `pve_node` defaults
    # to Proxmox's own default node name so a stock single-node install works
    # out of the box.
    pve_host: str = ""  # required, e.g. "192.168.1.10" or "pve.example.net"
    pve_port: int = 8006
    pve_node: str = "pve"
    pve_token_id: str = "hlidskjalf@pve!panel"
    pve_token_secret: str = ""
    # How https is verified: "pin" (SHA-256 fingerprint of one exact certificate —
    # the only option for a self-signed PVE cert) or "system" (normal CA chain +
    # hostname check — the right option behind an ACME cert, whose fingerprint
    # changes on every renewal and would take a pinned panel offline with it).
    pve_tls: str = "pin"
    # SHA-256 cert fingerprint, colon-separated hex. Empty disables pinning
    # (dev only — the mock PVE server speaks plain http).
    pve_fingerprint: str = ""
    # Scheme override for local dev against dev/mock_pve.py.
    pve_scheme: str = "https"

    # Panel auth — the bootstrap admin seeded into the DB on first run.
    admin_user: str = "admin"
    admin_password_hash: str = ""  # argon2id hash
    session_secret: str = ""
    session_max_age: int = 12 * 3600

    # --- Encryption at rest for stored secrets (see secretbox.py) -------------
    # Secrets the panel persists (the Proxmox token, the session key) are ALWAYS
    # encrypted in the database — there is no plaintext mode. This only chooses
    # where the key comes from:
    #
    #   set  -> the key lives outside the panel's disk (systemd LoadCredential /
    #           systemd-creds, a Docker or Kubernetes secret, a KMS). This is the
    #           mode that survives a stolen disk image or a leaked backup.
    #   unset-> the panel generates <state_dir>/secret.key (0600), kept in its own
    #           file separate from the database. Protects the realistic accident
    #           (someone copies just the .sqlite3); does NOT protect against an
    #           attacker who can already read the state dir as this user or root.
    # Supply as HLIDSKJALF_SECRET_KEY, or point at a file with
    # HLIDSKJALF_SECRET_KEY_FILE (see FILE_BACKED below).
    secret_key: str = ""
    # Set the `Secure` flag on the session cookie (cookie only sent over HTTPS).
    # Default True — safe for an internet-facing deploy. Set to False ONLY for
    # local plain-http dev/tests (Starlette's TestClient runs over http and will
    # not resend a Secure cookie).
    cookie_secure: bool = True

    # Behaviour — all site-specific, all opt-in. Defaults protect nothing and
    # assume no particular network, so a fresh deployment starts neutral.
    # comma-separated in the env var, e.g. "101,151"
    protected_vmids: Annotated[list[int], NoDecode] = []
    rescue_iso: str = ""  # e.g. "local:iso/systemrescue-12.01-amd64.iso"
    bandwidth_quotas: dict[str, int] = {}  # vmid (str) -> GB/month, display-only
    default_ssh_keys: str = ""
    # JSON env var, e.g. '{"20": "192.168.20.1", "30": ""}'. Empty = no VLAN tagging
    # offered in the provision form.
    vlan_gateways: dict[str, str] = {}
    clone_storage: str = "local-lvm"  # Proxmox's usual default storage
    # The bridge every panel-written net0 attaches to. "vmbr0" is Proxmox's
    # stock bridge name; real deployments often keep guests on another one
    # (verified 2026-07-13: the first real host runs its guests on vmbr1).
    pve_bridge: str = "vmbr0"

    # --- Update detection (routes/version.py) --------------------------------
    # The panel compares its running commit with the tip of `update_branch` in
    # `update_repo` on GitHub, and says so if it is behind. Detection only — it
    # never applies anything. Fail-soft: no network, no update offer, no error.
    # Nothing identifying is ever sent; it is an anonymous GET of a public repo.
    update_check_enabled: bool = True
    update_repo: str = "jivsan/Hlidskjalf"  # a fork can point this at itself
    update_branch: str = "main"

    # Applying an update from the UI (routes/update.py) EXECUTES CODE FETCHED FROM
    # THE INTERNET. That is the point of it, and it is why it is off by default and
    # cannot be turned on from inside the panel — only the operator on the host can.
    # Even when true it refuses anything but a clean, fast-forward git checkout whose
    # origin is `update_repo`, and it rolls back if the new code does not import.
    allow_self_update: bool = False
    # Whether a successful update re-execs the process. False = "applied, restart it
    # yourself" (right for a supervisor that pins the code, e.g. a read-only image).
    self_update_restart: bool = True

    # Metrics datasource: "rrd" (PVE rrddata, the default) or "prometheus"
    # (a Prometheus scraping prometheus-pve-exporter, see docs/prometheus.md).
    metrics_source: str = "rrd"

    # --- Prometheus datasource (only used with metrics_source=prometheus) ----
    # Base URL of the Prometheus HTTP API, WITHOUT the /api/v1 suffix, e.g.
    # "http://192.168.1.17:9090". Required when metrics_source=prometheus.
    prometheus_url: str = ""
    # Optional bearer token, sent as `Authorization: Bearer <token>` (for a
    # Prometheus behind an auth proxy). Leave empty for an unauthenticated one.
    prometheus_token: str = ""
    # Optional HTTP basic auth (alternative to the bearer token).
    prometheus_username: str = ""
    prometheus_password: str = ""
    # TLS for an https prometheus_url — same policy as the switch eAPI: pin the
    # cert by SHA-256 fingerprint (colon-separated hex) if it is self-signed,
    # else verify against system CAs (prometheus_verify=True, the default). Set
    # prometheus_verify=False only to knowingly disable verification (warns once).
    # Ignored for a plain-http URL (the common in-LAN case).
    prometheus_fingerprint: str = ""
    prometheus_verify: bool = True
    # HTTP timeout (seconds) for a query_range call.
    prometheus_timeout: float = 15.0
    # Optional PromQL for the node-series fields prometheus-pve-exporter does NOT
    # export (iowait, loadavg, netin, netout — PVE's /cluster/resources has no
    # such node fields). JSON map of node field -> PromQL expression; `$node` and
    # `$step` (the step in seconds) are substituted. Typically points at a
    # node_exporter on the PVE host. Fields left out stay None. e.g.
    #   {"loadavg": "node_load1{instance=\"pve:9100\"}"}
    prometheus_node_queries: dict[str, str] = {}

    # Logging & debug
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    debug: bool = False  # enables /api/debug/* and verbose error details for admins

    # Arista switch (7050TX etc.)
    # Recommended: enable eAPI on the switch:
    #   management api http-commands
    #     protocol https
    #     no shutdown
    # Then use username/password or token. Falls back to SSH if eapi disabled.
    # Empty switch_host disables the Switch section entirely (it is optional —
    # the panel is fully usable without a managed switch).
    switch_host: str = ""
    switch_port: int = 443
    switch_username: str = ""
    switch_password: str = ""
    # Switch eAPI TLS: the eAPI carries the switch admin credentials, so the
    # connection must be verified. Pin the switch cert by SHA-256 fingerprint
    # (colon-separated hex, like pve_fingerprint) — preferred for a self-signed
    # eAPI cert. If no fingerprint is set, verify against system CAs
    # (switch_verify=True). Set switch_verify=False only to knowingly disable
    # verification (logs a one-time warning; credentials go over an unverified
    # link — avoid for anything internet-facing).
    switch_fingerprint: str = ""
    switch_verify: bool = True

    # Paths
    static_dir: str = ""  # built frontend dist; empty = API only
    state_dir: str = "/var/lib/hlidskjalf"

    @field_validator("protected_vmids", mode="before")
    @classmethod
    def _split_vmids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v

    @field_validator("bandwidth_quotas", "vlan_gateways", "prometheus_node_queries", mode="before")
    @classmethod
    def _parse_json(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v

    @property
    def pve_base_url(self) -> str:
        return f"{self.pve_scheme}://{self.pve_host}:{self.pve_port}/api2/json"

    @property
    def db_path(self) -> Path:
        return Path(self.state_dir) / "hlidskjalf.sqlite3"


# Settings that may be supplied indirectly as `HLIDSKJALF_<NAME>_FILE=/path`.
# Secret managers hand you a FILE, not an environment variable — systemd
# LoadCredential, Docker/Compose secrets and Kubernetes all mount one — and a
# value passed through the environment is visible in /proc and leaks into logs
# and crash dumps far too easily.
FILE_BACKED = (
    "pve_token_secret",
    "session_secret",
    "admin_password_hash",
    "switch_password",
    "prometheus_token",
    "prometheus_password",
    "secret_key",
)


def _resolve_file_backed(settings: "Settings") -> None:
    """Load any `<FIELD>_FILE` settings from disk, in place."""
    import os

    for field in FILE_BACKED:
        path = os.environ.get(f"HLIDSKJALF_{field.upper()}_FILE", "").strip()
        if not path:
            continue
        p = Path(path)
        if not p.is_file():
            raise RuntimeError(
                f"HLIDSKJALF_{field.upper()}_FILE points at {path!r}, which does not exist."
            )
        object.__setattr__(settings, field, p.read_text().strip())
        # Mark it as env-provided so stored config never overrides it.
        settings.model_fields_set.add(field)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    _resolve_file_backed(s)
    return s


# Settings the first-run wizard is allowed to write. Deliberately a strict
# allowlist: the setup endpoint is unauthenticated (before setup completes), so it
# must never be able to reach anything outside this set.
SETUP_WRITABLE = frozenset(
    {
        "pve_host",
        "pve_port",
        "pve_node",
        "pve_scheme",
        "pve_token_id",
        "pve_token_secret",
        "pve_fingerprint",
        "pve_tls",
        "session_secret",
    }
)

# Settings an authenticated admin may edit at runtime (routes/settings.py).
# Deliberately a SEPARATE allowlist: SETUP_WRITABLE is reachable without
# credentials (first run only) and must never grow to cover these.
ADMIN_WRITABLE = frozenset(
    {
        "vlan_gateways",
        "clone_storage",
        "pve_bridge",
        # The Proxmox connection itself. It used to be settable ONLY in the
        # first-run wizard, which closes forever once a user exists — so a rotated
        # token, a renewed certificate or a moved host meant editing the database
        # by hand. An admin (with CSRF, and a live connection test that must pass
        # before anything is written) can change it now.
        "pve_host",
        "pve_port",
        "pve_node",
        "pve_scheme",
        "pve_token_id",
        "pve_token_secret",
        "pve_fingerprint",
        "pve_tls",
    }
)


def encryption_key(settings: Settings) -> str:
    """The key used for secrets at rest. Generates a local one on first call."""
    from . import secretbox

    return secretbox.resolve_key(settings.secret_key, settings.state_dir)


def seal(values: dict[str, str], settings: Settings) -> dict[str, str]:
    """Encrypt the secret-bearing entries of a config map, ready to persist."""
    from . import secretbox

    return secretbox.encrypt_config(values, encryption_key(settings))


def unseal(values: dict[str, str], settings: Settings) -> dict[str, str]:
    """Decrypt config loaded from the database back into usable plaintext."""
    from . import secretbox

    return secretbox.decrypt_config(values, encryption_key(settings))


def apply_stored(settings: Settings, stored: dict[str, str]) -> list[tuple[str, str, str]]:
    """Overlay config persisted by the setup wizard / admin settings onto
    `settings`, in place.

    **Environment always wins.** A field explicitly provided via env lands in
    ``model_fields_set``, and we skip those — so an operator keeping secrets in
    agenix / sops / systemd-creds is never overridden by whatever is in the DB.

    Returns the keys where env *shadowed* a different stored value, as
    ``(key, env_value, stored_value)``. Silence here is how a deployment ends up
    asking Proxmox for a node the operator never typed: the wizard saved one thing,
    an env default said another, and nothing ever said so out loud. The caller
    logs these.
    """
    shadowed: list[tuple[str, str, str]] = []
    from typing import get_origin

    for key, raw in stored.items():
        if key not in SETUP_WRITABLE | ADMIN_WRITABLE:
            continue  # ignore anything not on the allowlists
        current = getattr(settings, key, None)
        if key in settings.model_fields_set and current not in ("", None) and current != {}:
            # Set to a real value in the environment — leave it alone. An env var
            # defined but EMPTY (common in .env / compose files) is not a
            # configuration choice, so we let the stored value through instead of
            # leaving the panel permanently unconfigured.
            if raw and str(current) != str(raw) and key != "pve_token_secret":
                shadowed.append((key, str(current), str(raw)))
            continue
        field = Settings.model_fields.get(key)
        if field is None:
            continue
        value: object = raw
        if field.annotation is int:
            try:
                value = int(raw)
            except ValueError:
                continue
        elif field.annotation is bool:
            value = raw.strip().lower() in ("1", "true", "yes", "on")
        elif get_origin(field.annotation) is dict:
            # Dict fields (vlan_gateways) are stored as JSON, same as their env form.
            try:
                value = json.loads(raw) if raw.strip() else {}
            except ValueError:
                continue
        object.__setattr__(settings, key, value)
    return shadowed
