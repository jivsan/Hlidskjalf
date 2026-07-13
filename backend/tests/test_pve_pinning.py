"""TLS fingerprint pinning in pve.py.

A throwaway self-signed cert (via `cryptography`) backs a local TLS server
socket; the pinned client context must complete the handshake for the correct
SHA-256 fingerprint and abort it for a wrong one — on BOTH handshake paths:

- the memory-BIO path (`SSLContext.wrap_bio` -> `sslobject_class`), which is
  what asyncio/httpx/anyio and websockets use — i.e. what the panel uses;
- the plain-socket path (`SSLContext.wrap_socket` -> `sslsocket_class`), which
  the panel does not use today but which would silently skip the pin if only
  `sslobject_class` were set (real-hardware validation caught exactly that).
"""

import asyncio
import datetime
import hashlib
import socket
import ssl
import threading

import pytest

from hlidskjalf.config import Settings
from hlidskjalf.pve import (
    FingerprintMismatch,
    PveClient,
    _normalize_fp,
    make_pinned_ssl_context,
)

# --- unit-level checks -------------------------------------------------------


def test_normalize_fp():
    assert (
        _normalize_fp("AA:BB:cc:0d: 1E")
        == "aabbcc0d1e"
    )
    assert _normalize_fp("aabb") == "aabb"


def test_pinned_context_wiring_shape():
    ctx = make_pinned_ssl_context("aa" * 32)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE
    assert issubclass(ctx.sslobject_class, ssl.SSLObject)
    assert ctx.sslobject_class is not ssl.SSLObject  # the pinning subclass
    # wrap_socket() bypasses sslobject_class entirely; the pin must be wired
    # into sslsocket_class too or that path is a silent MITM hole.
    assert issubclass(ctx.sslsocket_class, ssl.SSLSocket)
    assert ctx.sslsocket_class is not ssl.SSLSocket  # the pinning subclass


def test_pveclient_refuses_https_without_fingerprint():
    settings = Settings(pve_scheme="https", pve_fingerprint="")
    with pytest.raises(RuntimeError, match="FINGERPRINT"):
        PveClient(settings)


# --- live handshake against a local self-signed TLS server -------------------


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    """(host, port, sha256_hex_fingerprint) of a local TLS echo-nothing server."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hlidskjalf-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    fingerprint = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()

    tmp = tmp_path_factory.mktemp("tls")
    cert_pem = tmp / "cert.pem"
    key_pem = tmp / "key.pem"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_pem, key_pem)

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.settimeout(0.2)
    host, port = sock.getsockname()
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(5)
                tls = server_ctx.wrap_socket(conn, server_side=True)
                tls.close()
            except Exception:
                # client aborting the handshake (wrong pin) lands here — fine
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    thread = threading.Thread(target=serve, name="tls-test-server", daemon=True)
    thread.start()
    yield host, port, fingerprint
    stop.set()
    thread.join(timeout=5)
    sock.close()


async def test_correct_fingerprint_connects(tls_server):
    host, port, fingerprint = tls_server
    # colon-separated uppercase, as copied from `openssl x509 -fingerprint`
    pin = ":".join(
        fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)
    ).upper()
    ctx = make_pinned_ssl_context(pin)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx), timeout=10
    )
    writer.close()
    try:
        await writer.wait_closed()
    except (ssl.SSLError, ConnectionError):
        pass  # server closes right after the handshake; only the handshake matters


async def test_wrong_fingerprint_aborts_handshake(tls_server):
    host, port, _ = tls_server
    ctx = make_pinned_ssl_context("00" * 32)
    with pytest.raises(FingerprintMismatch):
        await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx), timeout=10
        )


# --- the plain-socket path (wrap_socket / sslsocket_class) -------------------


def test_wrap_socket_correct_fingerprint_connects(tls_server):
    host, port, fingerprint = tls_server
    ctx = make_pinned_ssl_context(fingerprint)
    with socket.create_connection((host, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            assert tls.version() is not None  # handshake completed


def test_wrap_socket_wrong_fingerprint_aborts_handshake(tls_server):
    """FAILS if the pin is only enforced via sslobject_class (the original bug)."""
    host, port, _ = tls_server
    ctx = make_pinned_ssl_context("00" * 32)
    with socket.create_connection((host, port), timeout=10) as sock:
        with pytest.raises(FingerprintMismatch):
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
