"""Thin async Pangolin Integration-API client.

Optional integration: when configured (see `Settings.pangolin_enabled`), the panel
auto-creates a Pangolin **TCP** resource that tunnels SSH (port 22) to each VM it
provisions, and deletes that resource when the VM is destroyed. This module speaks
only the three routes that need — create resource, add target, delete resource.

Security posture (this key can create/delete Pangolin resources — treat it like the
PVE token):

- **SSH/TCP only.** `create_tcp_resource` always sends ``http=false`` and
  ``protocol="tcp"``. This client has no way to create a public HTTP resource, by
  design — the guardrail lives here and in routes/provision.py.
- **Identity routes are invite-only.** The user-sync methods below
  (`list_roles`, `create_invite`, `get_user_by_email`, `delete_org_user`,
  `delete_invitation`) manage org *membership* so panel tenants can pass the
  panel resource's Platform SSO wall. They never touch resources.
- **Invite links are bearer secrets.** `create_invite` requests
  ``sendEmail: false`` so the link returns to us, and it is handed to the
  admin exactly once — never stored in the DB, never logged (the error-path
  body logging below only fires on failure, where no link exists).
- **Bearer auth over verified TLS.** The api key rides in the ``Authorization``
  header; TLS is verified against the system CA store (a Pangolin API is a public
  HTTPS endpoint, not a self-signed box on the LAN). The key is NEVER logged.
- **Best-effort at the call site.** This client raises on failure; provision.py
  and routes/users.py catch so a Pangolin outage cannot fail a VM or user
  create/destroy.

Field names follow the documented Integration API shape. The API's real response
bodies are logged on error (they never contain the api key) so a wrong assumption
surfaces loudly rather than silently.
"""

import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("hlidskjalf.pangolin")


class PangolinError(Exception):
    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


def enabled(settings: Settings) -> bool:
    """Convenience mirror of Settings.pangolin_enabled for call sites that hold
    a settings object."""
    return settings.pangolin_enabled


def _extract(payload: Any, key: str) -> Any:
    """Pull `key` out of a Pangolin response, tolerating a ``{"data": {...}}``
    envelope or a flat object. Returns None if absent."""
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        data = payload.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


class PangolinClient:
    """One short-lived client per provision/destroy. Cheap to build; the whole
    integration is best-effort, so we do not keep a long-lived connection on
    app.state the way the PVE client is kept."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.pangolin_api_url.rstrip("/")
        self.org_id = settings.pangolin_org_id
        self.site_id = settings.pangolin_site_id
        # verify=True: system CA chain + hostname. A Pangolin API is a real HTTPS
        # service, not a pinned self-signed box.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {settings.pangolin_api_key}"},
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PangolinClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            # No response body here, and the api key lives in a header we never
            # format — safe to log the exception.
            raise PangolinError(f"Pangolin unreachable: {e}") from e
        if resp.status_code >= 400:
            body = resp.text
            # The response body is Pangolin's, not ours — it does not echo the
            # bearer token — so logging it is the whole point (defensive: surface
            # the real API error when a field name is wrong).
            log.warning(
                "Pangolin %s %s -> %s: %s", method, path, resp.status_code, body[:1000]
            )
            raise PangolinError(
                f"Pangolin {method} {path} failed ({resp.status_code})",
                status=resp.status_code,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    async def create_tcp_resource(self, name: str, proxy_port: int) -> int:
        """Create a TCP resource listening on `proxy_port`. Returns its numeric
        resourceId.

        GUARDRAIL: http is hardcoded False and protocol hardcoded "tcp". This
        client must never be able to create a public HTTP resource.
        """
        payload = await self._request(
            "PUT",
            f"/org/{self.org_id}/resource",
            json={
                "name": name,
                "http": False,
                "protocol": "tcp",
                "proxyPort": proxy_port,
            },
        )
        resource_id = _extract(payload, "resourceId")
        if resource_id is None:
            log.warning("Pangolin create resource returned no resourceId: %r", payload)
            raise PangolinError("Pangolin did not return a resourceId")
        return int(resource_id)

    async def add_target(self, resource_id: int, ip: str, port: int = 22) -> None:
        """Point the resource at `ip:port` reachable from the configured site."""
        await self._request(
            "PUT",
            f"/resource/{resource_id}/target",
            json={
                "siteId": self.site_id,
                "ip": ip,
                "port": port,
                "method": "tcp",
                "enabled": True,
            },
        )

    async def delete_resource(self, resource_id: int) -> None:
        await self._request("DELETE", f"/resource/{resource_id}")

    # --- tenant identity sync (org membership; never resources) --------------

    async def list_roles(self) -> list[dict]:
        """The org's roles, for resolving the configured tenant role by name."""
        payload = await self._request("GET", f"/org/{self.org_id}/roles")
        roles = _extract(payload, "roles")
        if isinstance(roles, list):
            return [r for r in roles if isinstance(r, dict)]
        # some Pangolin versions answer with a bare list
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    async def role_id_by_name(self, name: str) -> int:
        """Resolve the tenant role's name to its numeric roleId. A clear error
        here beats a confusing 400 from the invite call."""
        for r in await self.list_roles():
            rname = str(r.get("name", ""))
            rid = r.get("roleId", r.get("id"))
            if rname.lower() == name.lower() and rid is not None:
                return int(rid)
        raise PangolinError(
            f"tenant role '{name}' not found in the Pangolin org — "
            "create it there, or set HLIDSKJALF_PANGOLIN_TENANT_ROLE"
        )

    async def create_invite(self, email: str, role_id: int, valid_hours: int = 72) -> dict:
        """Invite `email` into the org under `role_id`.

        sendEmail is hardcoded False: the link comes back to the panel and the
        admin relays it out-of-band — no SMTP dependency, and the friend's
        password is chosen by the friend, never known to the panel.
        Returns {inviteLink, expiresAt, inviteId} — the LINK is a bearer
        secret; the caller must show it once and never store it.
        """
        payload = await self._request(
            "POST",
            f"/org/{self.org_id}/create-invite",
            json={
                "email": email,
                "roleId": role_id,
                "validHours": valid_hours,
                "sendEmail": False,
            },
        )
        link = _extract(payload, "inviteLink")
        if not link:
            log.warning("Pangolin create-invite returned no inviteLink: %r", payload)
            raise PangolinError("Pangolin did not return an inviteLink")
        return {
            "inviteLink": str(link),
            "expiresAt": _extract(payload, "expiresAt"),
            "inviteId": _extract(payload, "inviteId") or _extract(payload, "id"),
        }

    async def get_user_by_email(self, email: str) -> dict | None:
        """The org user with this email, or None.

        Email is the stable key: the invitee picks their username when they
        accept, so a username lookup would miss anyone who chose differently.
        The server's free-text filter narrows the list, then we exact-match
        client-side — a substring filter can return near-misses
        ("alice@example.com" is a substring of "malice@example.com").
        """
        payload = await self._request(
            "GET", f"/org/{self.org_id}/users", params={"query": email}
        )
        users = _extract(payload, "users")
        if not isinstance(users, list):
            return None
        for u in users:
            if isinstance(u, dict) and str(u.get("email", "")).lower() == email.lower():
                return u
        return None

    async def delete_org_user(self, user_id: str | int) -> None:
        """Remove a user from the org entirely (edge identity dies here)."""
        await self._request("DELETE", f"/org/{self.org_id}/user/{user_id}")

    async def delete_invitation(self, invite_id: str | int) -> None:
        """Cancel an invitation the friend never accepted."""
        await self._request("DELETE", f"/org/{self.org_id}/invitations/{invite_id}")
