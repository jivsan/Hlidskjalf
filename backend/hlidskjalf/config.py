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
    metrics_source: str = "rrd"  # rrd | prometheus (phase 2)

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

    @field_validator("bandwidth_quotas", "vlan_gateways", mode="before")
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
