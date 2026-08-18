"""Finds and verifies SNI candidates automatically, instead of an operator
hand-picking domains.

What automation can and cannot establish is worth being precise about,
because the two halves of "is this a good Reality dest" are not equally
mechanisable:

  verifiable        The domain really answers on 443, negotiates TLS 1.3,
                    offers h2, and presents a trusted certificate valid for
                    itself — and does so from the *node's* network position,
                    at a measured latency. Reality relays a prober's
                    handshake to this host, so a dest that is slow or broken
                    from the node degrades every connection to that inbound.
                    All of this is checked here.

  not verifiable    Whether traffic to the domain looks unremarkable. No
                    handshake reveals that. It comes from *sourcing*: domains
                    are drawn from a popularity ranking, so what lands in the
                    pool is what large numbers of ordinary users already
                    visit. A technically perfect but odd choice —
                    example.com passes every check above — is filtered by
                    where candidates come from, not by probing them.

The empirical correction is already in place: `SniCandidate.burn_count`
records how often an inbound on a domain was declared dead, and selection
prefers the least-burned. Discovery proposes; the block record disposes.
"""

from __future__ import annotations

import csv
import io
import logging
import socket
import ssl
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import Node, SniCandidate, SniProbe
from app.services.socks5 import socks5_connect

logger = logging.getLogger(__name__)

REQUIRED_TLS_VERSION = "TLSv1.3"
REQUIRED_ALPN = "h2"

# Special-use and documentation names. These can pass every TLS check while
# being obviously wrong to claim as a destination.
RESERVED_TLDS = frozenset({"test", "invalid", "localhost", "example", "local", "arpa", "onion"})
RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})


@dataclass(frozen=True)
class ProbeResult:
    domain: str
    ok: bool
    tls_version: str | None = None
    alpn: str | None = None
    latency_ms: int | None = None
    error: str | None = None


def is_plausible_dest(domain: str, own_domains: frozenset[str] = frozenset()) -> bool:
    """Cheap structural rejections, applied before spending a handshake."""
    domain = domain.strip().lower().rstrip(".")
    if not domain or " " in domain or "/" in domain:
        return False
    if domain in RESERVED_DOMAINS or domain in own_domains:
        return False

    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if labels[-1] in RESERVED_TLDS:
        return False
    if any(domain.endswith("." + own) for own in own_domains):
        return False

    # A bare IP address is not a name anyone browses to.
    try:
        socket.inet_aton(domain)
        return False
    except OSError:
        pass
    return True


def probe_domain(
    domain: str,
    *,
    port: int = 443,
    timeout: float = 6.0,
    socks_proxy: tuple[str, int] | None = None,
    cafile: str | None = None,
) -> ProbeResult:
    """Handshake with `domain` and decide whether it can serve as a dest.

    When `socks_proxy` is given the connection is made through it, which is
    how a probe is taken from a node's position rather than the head's.

    Certificate validation is deliberately left at the default strict
    setting: a destination whose certificate does not verify would make the
    relayed handshake fail for a prober, which defeats the point of choosing
    it. `cafile` overrides the trust store for deployments with a private CA.
    """
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2  # so a 1.2-only host is observed, not refused
    context.set_alpn_protocols([REQUIRED_ALPN, "http/1.1"])

    started = time.monotonic()
    try:
        if socks_proxy is not None:
            raw = socks5_connect(socks_proxy[0], socks_proxy[1], domain, port, timeout=timeout)
        else:
            raw = socket.create_connection((domain, port), timeout=timeout)

        raw.settimeout(timeout)
        with raw, context.wrap_socket(raw, server_hostname=domain) as tls:
            version = tls.version()
            alpn = tls.selected_alpn_protocol()
            latency_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - any failure to handshake is a rejected candidate, not an error to raise
        return ProbeResult(domain=domain, ok=False, error=f"{type(exc).__name__}: {exc}")

    if version != REQUIRED_TLS_VERSION:
        return ProbeResult(
            domain=domain,
            ok=False,
            tls_version=version,
            alpn=alpn,
            latency_ms=latency_ms,
            error=f"negotiated {version}, Reality needs {REQUIRED_TLS_VERSION}",
        )
    if alpn != REQUIRED_ALPN:
        return ProbeResult(
            domain=domain,
            ok=False,
            tls_version=version,
            alpn=alpn,
            latency_ms=latency_ms,
            error=f"ALPN {alpn!r}, Reality needs {REQUIRED_ALPN!r}",
        )

    return ProbeResult(
        domain=domain, ok=True, tls_version=version, alpn=alpn, latency_ms=latency_ms
    )


class DomainSource(Protocol):
    """Where candidate domains come from. This is the half that carries
    plausibility, so a source should rank by real-world popularity."""

    name: str

    def fetch(self, limit: int) -> list[str]: ...


class StaticSource:
    """Operator-supplied domains. Always available, needs no network."""

    name = "static"

    def __init__(self, domains: list[str]):
        self._domains = domains

    def fetch(self, limit: int) -> list[str]:
        return self._domains[:limit]


class TrancoSource:
    """Top domains from the Tranco list (https://tranco-list.eu).

    Tranco aggregates several popularity rankings and is research-grade and
    free, which makes it a defensible basis for "ordinary people visit this".
    Requires outbound HTTPS from the head; when that is unavailable the
    refresh falls back to whatever other sources are configured.
    """

    name = "tranco"
    URL = "https://tranco-list.eu/top-1m.csv.zip"

    def __init__(self, url: str | None = None, timeout: float = 60.0):
        self.url = url or self.URL
        self._timeout = timeout

    def fetch(self, limit: int) -> list[str]:
        response = httpx.get(self.url, timeout=self._timeout, follow_redirects=True)
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            name = archive.namelist()[0]
            with archive.open(name) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8")
                # rows are "rank,domain"; the file is ranked, so reading the
                # first `limit` rows is reading the most popular `limit`
                return [row[1] for _, row in zip(range(limit), csv.reader(text), strict=False) if len(row) > 1]


def _skip_rank_prefix(domains: list[str], skip: int) -> list[str]:
    """Drop the very top of a popularity ranking.

    The largest platforms make poor destinations despite being popular: they
    are the most likely to be individually handled by a censor, and several
    are themselves blocked in the target market, which would make an inbound
    unreachable for exactly the users it is meant to serve.
    """
    return domains[skip:]


def gather_candidates(sources: list[DomainSource], limit: int, skip_top: int) -> list[str]:
    seen: set[str] = set()
    gathered: list[str] = []
    own = frozenset(get_settings().own_domains)

    for source in sources:
        try:
            fetched = source.fetch(limit + skip_top)
        except Exception:
            logger.exception("domain source %s failed, continuing with the others", source.name)
            continue

        for domain in _skip_rank_prefix(fetched, skip_top if source.name != "static" else 0):
            domain = domain.strip().lower()
            if domain in seen or not is_plausible_dest(domain, own):
                continue
            seen.add(domain)
            gathered.append(domain)
            if len(gathered) >= limit:
                return gathered
    return gathered


def _node_proxy(node: Node) -> tuple[str, int] | None:
    """A SOCKS proxy egressing at `node`, via its Reality control tunnel."""
    from app.node_manager.channel import tunnel_socks_proxy  # circular at module level

    return tunnel_socks_proxy(node)


def refresh_candidates(db: Session, sources: list[DomainSource]) -> int:
    """Pull domains from the sources into the candidate pool. Returns how many are new."""
    settings = get_settings()
    domains = gather_candidates(sources, settings.sni_pool_size, settings.sni_skip_top_ranks)

    known = set(db.scalars(select(SniCandidate.domain)).all())
    added = 0
    for domain in domains:
        if domain in known:
            continue
        db.add(SniCandidate(domain=domain, source="auto"))
        added += 1
    db.flush()
    logger.info("candidate pool: %d fetched, %d new", len(domains), added)
    return added


def probe_candidates_for_node(db: Session, node: Node, limit: int | None = None) -> list[ProbeResult]:
    """Verify candidates from `node`'s vantage point and record the verdicts.

    Probing runs through the node's Reality tunnel when one can be
    established; otherwise it falls back to probing from the head, which
    still catches a dead or non-TLS-1.3 domain but cannot measure what the
    node would see. The fallback is recorded so the two are not confused.
    """
    settings = get_settings()
    limit = limit or settings.sni_probe_batch

    candidates = db.scalars(
        select(SniCandidate).where(SniCandidate.active.is_(True)).limit(limit)
    ).all()

    try:
        proxy = _node_proxy(node)
    except Exception:
        logger.exception("could not open a tunnel to node %s, probing from the head instead", node.id)
        proxy = None

    results = []
    for candidate in candidates:
        result = probe_domain(candidate.domain, timeout=settings.sni_probe_timeout_s, socks_proxy=proxy)
        results.append(result)

        existing = db.scalar(
            select(SniProbe).where(SniProbe.node_id == node.id, SniProbe.candidate_id == candidate.id)
        )
        if existing is None:
            existing = SniProbe(node_id=node.id, candidate_id=candidate.id)
            db.add(existing)

        existing.ok = result.ok
        existing.tls_version = result.tls_version
        existing.alpn = result.alpn
        existing.latency_ms = result.latency_ms
        existing.error = result.error
        existing.from_node = proxy is not None
        existing.checked_at = datetime.now(UTC)

    db.flush()
    logger.info(
        "node %s: %d/%d candidates usable", node.id, sum(1 for r in results if r.ok), len(results)
    )
    return results


def default_sources() -> list[DomainSource]:
    settings = get_settings()
    sources: list[DomainSource] = []
    if settings.sni_use_tranco:
        sources.append(TrancoSource())
    if settings.sni_seed_domains:
        sources.append(StaticSource(list(settings.sni_seed_domains)))
    return sources
