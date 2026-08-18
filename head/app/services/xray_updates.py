"""Keeping Xray on the nodes current, without ever surprising anyone.

The rule this module exists to enforce: the head may *notice* a new Xray
release on its own, but it may not install one on its own. Applying an
update recreates the node's container, which drops every live connection on
that node — so a human approves each one, in Telegram or in the admin panel,
and only then does the head act. That is why detection and application are
two separate passes with a database row in between (app/db/models/update.py)
rather than one function that checks and upgrades.

Two details are worth knowing before reading the code:

* The node's Xray does not come from XTLS directly — it ships inside the
  `gozargah/marzban-node` image. So "update available upstream" and "an
  update the node can actually take" are different questions, and the image
  can legitimately lag a release by days. `_suppress_until` and the
  `version_after < target_version` case below both exist because of that
  gap: without them a release the image has not picked up yet would raise a
  fresh proposal on every single check and nag the operator forever.

* Version comparison is numeric, not lexicographic. `9.1.0` is older than
  `26.3.27`, which string comparison gets backwards — and Xray's versions
  crossed from 1.x to 25.x, so this is not hypothetical.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import Node, NodeStatus
from app.db.models.update import OPEN_STATUSES, NodeUpdate, NodeUpdateStatus
from app.node_manager.channel import call_node
from app.services import ssh_manager
from app.services.certs import bundle_for
from app.services.ssh_manager import SshError
from app.services.timeutil import as_aware

logger = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"

# The head image carries provisioning/ at a fixed path (see head/Dockerfile);
# the update script is its sibling, so there is no second path to configure
# and get wrong.
UPDATE_SCRIPT_NAME = "update_node.sh"

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")

# Cached because the check loop runs per node and GitHub rate-limits
# unauthenticated callers at 60 requests an hour. One lookup per pass is
# plenty — the answer changes at most daily.
_cache: dict[str, tuple[float, str | None]] = {}


class XrayUpdateError(RuntimeError):
    pass


# --- versions ------------------------------------------------------------


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """Pull a comparable version tuple out of whatever form we were handed.

    Accepts a bare `26.3.27`, a tag `v26.3.27`, and the first line of
    `xray version` (`Xray 26.3.27 (Xray, Penetrates Everything.)`), because
    all three reach this module from different directions.
    """
    if not text:
        return None
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def normalise_version(text: str | None) -> str | None:
    parsed = parse_version(text)
    return ".".join(str(p) for p in parsed) if parsed else None


def is_newer(candidate: str | None, current: str | None) -> bool:
    """True when `candidate` is a strictly newer version than `current`.

    An unknown `current` is deliberately *not* treated as "outdated": a node
    whose control channel is down reports nothing, and proposing an update
    for a node we cannot even reach would ask an operator to authorise a
    restart of something we know nothing about.
    """
    new = parse_version(candidate)
    old = parse_version(current)
    if new is None or old is None:
        return False
    return new > old


def latest_release_version(*, force: bool = False, cached_only: bool = False) -> str | None:
    """The newest Xray-core release tag, or None if the feed is unreachable.

    None is a normal outcome, not an error: the head sits in a jurisdiction
    where GitHub is intermittently reachable at best, and a failed lookup
    should mean "no update news this pass", never a failed pass.

    `cached_only` is for callers a human is waiting on — the admin page. On
    a head that cannot reach GitHub every lookup costs the full timeout, and
    a page that takes ten seconds to render because of a decoration in its
    corner is a worse page than one that says "unknown".
    """
    settings = get_settings()
    ttl = settings.xray_release_cache_minutes * 60
    cached = _cache.get("latest")
    if cached and not force and time.monotonic() - cached[0] < ttl:
        return cached[1]
    if cached_only:
        return cached[1] if cached else None

    version: str | None = None
    try:
        resp = httpx.get(
            RELEASES_URL,
            timeout=10.0,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "freeskyvpn-head"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        version = normalise_version(resp.json().get("tag_name"))
    except Exception:
        logger.warning("could not read the Xray release feed; skipping this check", exc_info=True)
        # Do not cache a failure for the full TTL, otherwise one bad minute
        # blinds the head for hours.
        return cached[1] if cached else None

    _cache["latest"] = (time.monotonic(), version)
    return version


# --- detection -----------------------------------------------------------


def node_core_version(db: Session, node: Node) -> str | None:
    """What Xray the node is actually running, asked over the control channel."""
    try:
        status = call_node(db, node, bundle_for(node), lambda client: client.status())
    except Exception:
        logger.info("node %s did not answer a version query", node.id, exc_info=True)
        return None
    return normalise_version(status.core_version)


def _suppress_until(db: Session, node: Node, target: str) -> bool:
    """Should a proposal for this node/version be skipped this pass?

    Skipped when one is already open (nothing to add), when an operator
    already declined this exact version (asking again is nagging), and for a
    cooldown after a failed or ineffective attempt — the marzban-node image
    lagging upstream is the common case, and re-proposing every twelve hours
    would train the operator to ignore the notification.
    """
    settings = get_settings()
    existing = db.scalars(
        select(NodeUpdate)
        .where(NodeUpdate.node_id == node.id, NodeUpdate.target_version == target)
        .order_by(NodeUpdate.created_at.desc())
    ).all()

    cooldown = timedelta(hours=settings.xray_update_retry_hours)

    for row in existing:
        if row.status in OPEN_STATUSES:
            return True  # already asked about, or already queued
        if row.status == NodeUpdateStatus.declined:
            return True  # they said no; asking again is nagging
        if row.status in (NodeUpdateStatus.applied, NodeUpdateStatus.failed):
            # `applied` only reaches here when the node is *still* behind the
            # target, which means the marzban-node image has not picked the
            # release up yet. Both that and an outright failure are worth
            # retrying eventually — images do catch up, and a failure can be
            # transient — but not on the next twelve-hourly pass, or the
            # operator learns to ignore the notification.
            finished = as_aware(row.finished_at or row.created_at)
            if finished is not None and datetime.now(UTC) - finished < cooldown:
                return True
    return False


def check_for_updates(db: Session) -> list[NodeUpdate]:
    """One detection pass. Returns the proposals it raised (possibly none)."""
    target = latest_release_version()
    if target is None:
        return []

    nodes = db.scalars(select(Node).where(Node.status == NodeStatus.active)).all()
    raised: list[NodeUpdate] = []

    for node in nodes:
        current = node_core_version(db, node)
        if not is_newer(target, current):
            continue
        if _suppress_until(db, node, target):
            continue

        proposal = NodeUpdate(node_id=node.id, target_version=target, version_before=current)
        db.add(proposal)
        raised.append(proposal)
        logger.info("node %s: Xray %s -> %s available", node.id, current, target)

    db.flush()
    return raised


# --- decisions -----------------------------------------------------------


def _apply_decision(db: Session, rows, *, approve: bool, by: str) -> int:
    for row in rows:
        row.status = NodeUpdateStatus.approved if approve else NodeUpdateStatus.declined
        row.decided_at = datetime.now(UTC)
        row.decided_by = by[:64]
    db.flush()
    return len(rows)


def decide(db: Session, update_ids: list, *, approve: bool, by: str) -> int:
    """Record an operator's answer on specific rows. Returns how many moved.

    Only `pending` rows move, so a second tap on the same Telegram button —
    or an approval that races the admin panel — is a no-op rather than a
    second restart of the node.
    """
    rows = db.scalars(
        select(NodeUpdate).where(
            NodeUpdate.id.in_(update_ids), NodeUpdate.status == NodeUpdateStatus.pending
        )
    ).all()
    return _apply_decision(db, rows, approve=approve, by=by)


def decide_version(db: Session, target_version: str, *, approve: bool, by: str) -> int:
    """Answer for every node still waiting on one version.

    This is what a Telegram button resolves to. It has to be by version
    rather than by row id because callback_data is capped at 64 bytes, which
    a handful of UUIDs exceeds — but it is also the better semantic: the
    operator was asked about a release, not about a list of rows, and any
    node that joined the queue between the question and the answer belongs
    under the same decision.
    """
    rows = db.scalars(
        select(NodeUpdate).where(
            NodeUpdate.target_version == target_version,
            NodeUpdate.status == NodeUpdateStatus.pending,
        )
    ).all()
    return _apply_decision(db, rows, approve=approve, by=by)


# --- application ---------------------------------------------------------


@dataclass
class UpdateOutcome:
    ok: bool
    before: str | None
    after: str | None
    error: str | None


def _update_script_source() -> str:
    from app.services.provisioning import bootstrap_script_path

    path = Path(bootstrap_script_path()).parent / UPDATE_SCRIPT_NAME
    if not path.is_file():
        raise XrayUpdateError(f"update script not found at {path}")
    return path.read_text()


def _run_update_on_node(node: Node) -> UpdateOutcome:
    script = _update_script_source()
    with ssh_manager.connect(node, timeout=30.0) as client:
        result = ssh_manager.run(client, "bash -s", stdin_data=script)

    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise XrayUpdateError(
            f"the update script produced no output (exit {result.exit_status}): "
            f"{result.stderr.strip()[-500:]}"
        )
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise XrayUpdateError(f"could not parse the update script's output: {lines[-1][:300]}") from exc

    return UpdateOutcome(
        ok=bool(payload.get("ok")),
        before=normalise_version(payload.get("before")),
        after=normalise_version(payload.get("after")),
        error=payload.get("error") or None,
    )


def apply_update(db: Session, update: NodeUpdate) -> NodeUpdate:
    """Run one approved update. Never raises — the outcome lands on the row.

    Swallowing the exception is the point: this is called from a background
    loop where an unhandled error would abandon the row in `applying` and
    stall every update behind it.
    """
    node = db.get(Node, update.node_id)
    update.status = NodeUpdateStatus.applying
    update.started_at = datetime.now(UTC)
    db.flush()

    if node is None:
        update.status = NodeUpdateStatus.failed
        update.error = "нода удалена"
        update.finished_at = datetime.now(UTC)
        db.flush()
        return update

    try:
        outcome = _run_update_on_node(node)
    except (SshError, XrayUpdateError, OSError) as exc:
        update.status = NodeUpdateStatus.failed
        update.error = str(exc)[:1000]
        update.finished_at = datetime.now(UTC)
        db.flush()
        logger.warning("Xray update failed on node %s: %s", node.id, exc)
        return update

    update.version_before = outcome.before or update.version_before
    update.version_after = outcome.after
    update.finished_at = datetime.now(UTC)

    if not outcome.ok:
        update.status = NodeUpdateStatus.failed
        update.error = (outcome.error or "обновление не завершилось")[:1000]
        db.flush()
        return update

    update.status = NodeUpdateStatus.applied
    if is_newer(update.target_version, outcome.after):
        # Ran fine, but the image is still behind the release. Recorded as a
        # note rather than a failure, because nothing is broken and there is
        # nothing an operator can do about it except wait.
        update.error = (
            f"нода обновилась до {outcome.after}, "
            f"образ marzban-node ещё не содержит {update.target_version}"
        )
    db.flush()
    logger.info("node %s: Xray %s -> %s", node.id, outcome.before, outcome.after)
    return update


def apply_approved(db: Session, limit: int = 1) -> list[NodeUpdate]:
    """Apply approved updates, oldest first.

    One node per pass by default, and deliberately so: each update restarts
    a node's Xray, and doing several at once would take a chunk of the fleet
    down together. Spreading them over successive passes means users who get
    moved off a restarting node have somewhere to land.
    """
    rows = db.scalars(
        select(NodeUpdate)
        .where(NodeUpdate.status == NodeUpdateStatus.approved)
        .order_by(NodeUpdate.decided_at.asc())
        .limit(limit)
    ).all()

    return [apply_update(db, row) for row in rows]
