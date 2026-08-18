"""The probe is judged on what it *rejects*, so these run against real TLS
servers configured to fail one requirement each — see tests/tls_server.py."""

import ssl

import pytest

from app.services.sni_discovery import (
    StaticSource,
    gather_candidates,
    is_plausible_dest,
    probe_domain,
)
from tests.tls_server import make_cert, tls_server


@pytest.fixture
def good_cert(tmp_path):
    return make_cert(tmp_path, "localhost")


def test_accepts_tls13_with_h2(good_cert):
    with tls_server(good_cert, alpn=["h2"]) as port:
        result = probe_domain("localhost", port=port, cafile=str(good_cert.cert))

    assert result.ok is True
    assert result.tls_version == "TLSv1.3"
    assert result.alpn == "h2"
    assert result.latency_ms is not None


def test_rejects_tls12_only(good_cert):
    with tls_server(
        good_cert,
        alpn=["h2"],
        min_version=ssl.TLSVersion.TLSv1_2,
        max_version=ssl.TLSVersion.TLSv1_2,
    ) as port:
        result = probe_domain("localhost", port=port, cafile=str(good_cert.cert))

    assert result.ok is False
    assert result.tls_version == "TLSv1.2"
    assert "TLSv1.3" in result.error


def test_rejects_host_without_h2(good_cert):
    with tls_server(good_cert, alpn=["http/1.1"]) as port:
        result = probe_domain("localhost", port=port, cafile=str(good_cert.cert))

    assert result.ok is False
    assert result.alpn == "http/1.1"
    assert "h2" in result.error


def test_rejects_untrusted_certificate(good_cert):
    """No cafile: the self-signed cert is not in the system trust store."""
    with tls_server(good_cert, alpn=["h2"]) as port:
        result = probe_domain("localhost", port=port)

    assert result.ok is False
    assert "verify" in result.error.lower() or "certificate" in result.error.lower()


def test_rejects_certificate_for_a_different_name(tmp_path):
    other = make_cert(tmp_path, "not-the-domain.example")
    with tls_server(other, alpn=["h2"]) as port:
        result = probe_domain("localhost", port=port, cafile=str(other.cert))

    assert result.ok is False
    assert "hostname" in result.error.lower() or "match" in result.error.lower()


def test_rejects_unreachable_host():
    result = probe_domain("localhost", port=1, timeout=2)
    assert result.ok is False
    assert result.tls_version is None


# --- structural filtering, applied before any handshake -------------------


@pytest.mark.parametrize(
    "domain",
    ["example.com", "example.net", "example.org", "foo.test", "bar.invalid", "thing.local", "192.0.2.1", "nodots"],
)
def test_implausible_destinations_are_filtered_out(domain):
    assert is_plausible_dest(domain) is False


@pytest.mark.parametrize("domain", ["www.samsung.com", "cdn.jsdelivr.net", "static.example-corp.io"])
def test_ordinary_domains_pass_the_structural_filter(domain):
    assert is_plausible_dest(domain) is True


def test_our_own_domains_are_never_proposed():
    own = frozenset({"freeskyvpn.example"})
    assert is_plausible_dest("freeskyvpn.example", own) is False
    assert is_plausible_dest("node1.freeskyvpn.example", own) is False
    assert is_plausible_dest("someone-else.com", own) is True


# --- sourcing ------------------------------------------------------------


def test_gathering_skips_the_very_top_of_the_ranking():
    """The biggest platforms are the likeliest to be individually handled."""
    ranked = [f"site{i}.com" for i in range(20)]
    source = StaticSource(ranked)

    # a static source is operator intent, so the skip does not apply to it
    assert gather_candidates([source], limit=5, skip_top=3) == ranked[:5]


def test_gathering_deduplicates_and_filters(monkeypatch):
    class Ranked:
        name = "ranked"

        def fetch(self, limit):
            return ["a.com", "a.com", "example.com", "b.test", "c.com"]

    result = gather_candidates([Ranked()], limit=10, skip_top=0)

    assert result == ["a.com", "c.com"], "duplicates and reserved names are dropped"


def test_a_failing_source_does_not_abort_the_others():
    class Broken:
        name = "broken"

        def fetch(self, limit):
            raise RuntimeError("network down")

    result = gather_candidates([Broken(), StaticSource(["good.com"])], limit=5, skip_top=0)
    assert result == ["good.com"]
