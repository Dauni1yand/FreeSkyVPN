"""Real TLS servers with deliberately chosen properties, for testing the probe.

The probe's whole job is discrimination, so testing it against mocks would
verify nothing. These are genuine TLS listeners on loopback, each configured
to fail exactly one of the probe's requirements — a real handshake really is
performed against them.

Loopback specifically: an intercepting egress proxy (which some environments
run) re-issues every certificate and can rewrite the negotiated parameters,
so probing a public domain from inside such an environment measures the
proxy, not the domain. Loopback traffic never reaches it.
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CertPair:
    cert: Path
    key: Path


def make_cert(tmp_path: Path, common_name: str = "localhost", san: str | None = None) -> CertPair:
    cert = tmp_path / f"{common_name}.crt"
    key = tmp_path / f"{common_name}.key"
    san = san or f"DNS:{common_name}"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-subj", f"/CN={common_name}",
            "-addext", f"subjectAltName={san}",
        ],
        check=True,
        capture_output=True,
    )
    return CertPair(cert=cert, key=key)


@contextmanager
def tls_server(
    certs: CertPair,
    *,
    alpn: list[str] | None = None,
    max_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3,
    min_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2,
):
    """Run a one-shot TLS listener on loopback; yields its port."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certs.cert), str(certs.key))
    context.minimum_version = min_version
    context.maximum_version = max_version
    if alpn is not None:
        context.set_alpn_protocols(alpn)

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.3)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            try:
                with context.wrap_socket(conn, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()
