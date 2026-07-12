"""All configuration from environment variables, prefix HLIDSKJALF_."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HLIDSKJALF_")

    # Proxmox API
    pve_host: str = "10.0.20.10"
    pve_port: int = 8006
    pve_node: str = "hella"
    pve_token_id: str = "hlidskjalf@pve!panel"
    pve_token_secret: str = ""
    # SHA-256 cert fingerprint, colon-separated hex. Empty disables pinning
    # (dev only — the mock PVE server speaks plain http).
    pve_fingerprint: str = ""
    # Scheme override for local dev against dev/mock_pve.py.
    pve_scheme: str = "https"

    # Panel auth (single admin user)
    admin_user: str = "christina"
    admin_password_hash: str = ""  # argon2id hash
    session_secret: str = ""
    session_max_age: int = 12 * 3600

    # Behaviour
    # comma-separated in the env var, e.g. "101,151"
    protected_vmids: Annotated[list[int], NoDecode] = [151]
    rescue_iso: str = ""  # e.g. "local:iso/systemrescue-12.01-amd64.iso"
    bandwidth_quotas: dict[str, int] = {}  # vmid (str) -> GB/month, display-only
    default_ssh_keys: str = ""
    vlan_gateways: dict[str, str] = {"20": "10.0.20.1", "30": "", "50": "10.0.50.1"}
    clone_storage: str = "local-lvm"
    metrics_source: str = "rrd"  # rrd | prometheus (phase 2)

    # Arista switch (7050TX etc.)
    # Recommended: enable eAPI on the switch:
    #   management api http-commands
    #     protocol https
    #     no shutdown
    # Then use username/password or token. Falls back to SSH if eapi disabled.
    switch_host: str = "10.0.20.2"
    switch_port: int = 443
    switch_username: str = ""
    switch_password: str = ""
    switch_use_eapi: bool = True  # preferred over SSH
    switch_ssh_port: int = 22

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
