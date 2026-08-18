"""The SOCKS5 client is hand-written, so it is tested against a real proxy
that actually relays bytes rather than against a mock of itself."""

import pytest

from app.services.sni_discovery import probe_domain
from app.services.socks5 import Socks5Error, socks5_connect
from tests.socks_server import socks5_server
from tests.tls_server import make_cert, tls_server


def test_connect_reaches_the_upstream_service(tmp_path):
    certs = make_cert(tmp_path, "localhost")
    with tls_server(certs, alpn=["h2"]) as tls_port, socks5_server(upstream_port=tls_port) as proxy_port:
        sock = socks5_connect("127.0.0.1", proxy_port, "localhost", 443, timeout=5)
        try:
            assert sock.fileno() != -1
        finally:
            sock.close()


def test_probe_works_end_to_end_through_the_proxy(tmp_path):
    """The whole point of the SOCKS client: probing from a node's position."""
    certs = make_cert(tmp_path, "localhost")
    with tls_server(certs, alpn=["h2"]) as tls_port, socks5_server(upstream_port=tls_port) as proxy_port:
        result = probe_domain(
            "localhost",
            port=443,  # what the proxy is asked for; it redirects to the test listener
            cafile=str(certs.cert),
            socks_proxy=("127.0.0.1", proxy_port),
            timeout=5,
        )

    assert result.ok is True
    assert result.tls_version == "TLSv1.3"
    assert result.alpn == "h2"


def test_probe_through_a_proxy_still_rejects_a_bad_host(tmp_path):
    """Tunnelling must not weaken the checks."""
    certs = make_cert(tmp_path, "localhost")
    with tls_server(certs, alpn=["http/1.1"]) as tls_port, socks5_server(upstream_port=tls_port) as proxy_port:
        result = probe_domain(
            "localhost", port=443, cafile=str(certs.cert), socks_proxy=("127.0.0.1", proxy_port), timeout=5
        )

    assert result.ok is False
    assert "h2" in result.error


@pytest.mark.parametrize(
    ("reply_code", "expected"),
    [(0x05, "connection refused"), (0x04, "host unreachable"), (0x02, "not allowed")],
)
def test_socks_error_replies_are_reported(reply_code, expected):
    with socks5_server(force_reply=reply_code) as proxy_port, pytest.raises(Socks5Error) as excinfo:
        socks5_connect("127.0.0.1", proxy_port, "somewhere.example", 443, timeout=5)
    assert expected in str(excinfo.value)


def test_unreachable_proxy_raises_rather_than_hanging():
    with pytest.raises(OSError):
        socks5_connect("127.0.0.1", 1, "somewhere.example", 443, timeout=2)


def test_probe_reports_a_proxy_failure_instead_of_claiming_success():
    result = probe_domain("somewhere.example", socks_proxy=("127.0.0.1", 1), timeout=2)
    assert result.ok is False
    assert result.error is not None
