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
    # JSON env var, e.g. '{"20": "10.0.20.1", "30": ""}'. Empty = no VLAN tagging
    # offered in the provision form.
    vlan_gateways: dict[str, str] = {}
    clone_storage: str = "local-lvm"  # Proxmox's usual default storage

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


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
        "session_secret",
    }
)


def apply_stored(settings: Settings, stored: dict[str, str]) -> None:
    """Overlay config persisted by the setup wizard onto `settings`, in place.

    **Environment always wins.** A field explicitly provided via env lands in
    ``model_fields_set``, and we skip those — so an operator keeping secrets in
    agenix / sops / systemd-creds is never overridden by whatever is in the DB.
    """
    for key, raw in stored.items():
        if key not in SETUP_WRITABLE:
            continue  # ignore anything not on the allowlist
        if key in settings.model_fields_set and getattr(settings, key, None) not in ("", None):
            # Set to a real value in the environment — leave it alone. An env var
            # defined but EMPTY (common in .env / compose files) is not a
            # configuration choice, so we let the stored value through instead of
            # leaving the panel permanently unconfigured.
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
        object.__setattr__(settings, key, value)
