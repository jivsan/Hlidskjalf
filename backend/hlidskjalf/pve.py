"""Thin async Proxmox VE API client.

Auth via API token header; TLS verified against a pinned SHA-256 certificate
fingerprint instead of a CA chain (hella's cert is self-signed). The pin is
enforced inside the TLS handshake on BOTH of Python's handshake paths: a custom
SSLObject subclass covers the memory-BIO path (`SSLContext.wrap_bio` — what
httpx (REST) and websockets (VNC proxy) use), and a custom SSLSocket subclass
covers the plain-socket path (`SSLContext.wrap_socket`), so no caller can
bypass the pin by picking the other API.
"""

import asyncio
import hashlib
import ssl
from typing import Any

import httpx

from .config import Settings


class PveError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class FingerprintMismatch(ssl.SSLError):
    pass


def _normalize_fp(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").lower()


def _check_pinned_cert(der: bytes | None, expected: str) -> None:
    """Raise FingerprintMismatch unless `der` hashes (SHA-256) to `expected`."""
    if der is None or hashlib.sha256(der).hexdigest() != expected:
        raise FingerprintMismatch(
            "PVE certificate SHA-256 fingerprint does not match the pinned value"
        )


def make_pinned_ssl_context(fingerprint: str) -> ssl.SSLContext:
    """TLS context that accepts exactly one certificate: the pinned one.

    Chain/hostname verification is disabled (self-signed cert), and instead the
    peer cert's SHA-256 digest is compared during the handshake. A mismatch
    aborts the connection before any request data is sent.

    The check is wired into BOTH handshake paths: `sslobject_class` covers the
    memory-BIO path (`SSLContext.wrap_bio` — asyncio/httpx/websockets, i.e.
    everything the panel does), and `sslsocket_class` covers the plain-socket
    path (`SSLContext.wrap_socket`). `sslobject_class` alone does NOT apply to
    `wrap_socket`, so without the second hook any future caller using it would
    silently get an unpinned connection.
    """
    expected = _normalize_fp(fingerprint)

    class PinnedSSLObject(ssl.SSLObject):
        def do_handshake(self) -> None:
            super().do_handshake()
            _check_pinned_cert(self.getpeercert(binary_form=True), expected)

    class PinnedSSLSocket(ssl.SSLSocket):
        def do_handshake(self, *args, **kwargs) -> None:
            super().do_handshake(*args, **kwargs)
            _check_pinned_cert(self.getpeercert(binary_form=True), expected)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.sslobject_class = PinnedSSLObject
    ctx.sslsocket_class = PinnedSSLSocket
    return ctx


class PveClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.node = settings.pve_node
        if settings.pve_scheme == "https":
            if not settings.pve_fingerprint:
                raise RuntimeError(
                    "HLIDSKJALF_PVE_FINGERPRINT is required with https "
                    "(refusing to connect unverified)"
                )
            self.ssl_context: ssl.SSLContext | None = make_pinned_ssl_context(
                settings.pve_fingerprint
            )
        else:
            self.ssl_context = None
        self._client = httpx.AsyncClient(
            base_url=settings.pve_base_url,
            headers={
                "Authorization": f"PVEAPIToken={settings.pve_token_id}={settings.pve_token_secret}"
            },
            verify=self.ssl_context if self.ssl_context else False,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise PveError(502, f"PVE unreachable: {e}") from e
        if resp.status_code >= 400:
            detail = resp.reason_phrase
            try:
                body = resp.json()
                if body.get("errors"):
                    detail = f"{detail}: {body['errors']}"
                elif body.get("message"):
                    detail = f"{detail}: {body['message']}"
            except Exception:
                pass
            raise PveError(resp.status_code, f"PVE {method} {path} failed ({detail})")
        return resp.json().get("data")

    async def get(self, path: str, **params) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, **data) -> Any:
        return await self.request("POST", path, data={k: v for k, v in data.items() if v is not None})

    async def put(self, path: str, **data) -> Any:
        return await self.request("PUT", path, data={k: v for k, v in data.items() if v is not None})

    async def delete(self, path: str, **params) -> Any:
        return await self.request("DELETE", path, params={k: v for k, v in params.items() if v is not None})

    # --- convenience wrappers -------------------------------------------------

    async def cluster_resources(self, type_: str | None = "vm") -> list[dict]:
        return await self.get("/cluster/resources", type=type_)

    def _guest_base(self, vmid: int, kind: str = "qemu") -> str:
        return f"/nodes/{self.node}/{kind}/{vmid}"

    async def vm_current(self, vmid: int, kind: str = "qemu") -> dict:
        return await self.get(f"{self._guest_base(vmid, kind)}/status/current")

    async def vm_config(self, vmid: int, kind: str = "qemu") -> dict:
        return await self.get(f"{self._guest_base(vmid, kind)}/config")

    async def task_status(self, upid: str) -> dict:
        return await self.get(f"/nodes/{self.node}/tasks/{upid}/status")

    async def wait_task(self, upid: str, timeout: float = 300.0, interval: float = 1.0) -> dict:
        """Poll a UPID until it stops; raise PveError if it did not exit OK."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            status = await self.task_status(upid)
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise PveError(500, f"PVE task {upid} failed: {status.get('exitstatus')}")
                return status
            if asyncio.get_event_loop().time() > deadline:
                raise PveError(504, f"PVE task {upid} timed out after {timeout}s")
            await asyncio.sleep(interval)

    def guest_kind(self, resource: dict) -> str:
        """'qemu' or 'lxc' from a /cluster/resources entry."""
        return "lxc" if resource.get("type") == "lxc" else "qemu"

    async def find_resource(self, vmid: int) -> dict | None:
        for r in await self.cluster_resources():
            if r.get("vmid") == vmid:
                return r
        return None
