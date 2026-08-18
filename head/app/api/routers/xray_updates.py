"""Xray update proposals, as the bot sees them.

The head detects updates and applies approved ones; it cannot talk to
Telegram interactively, so the bot is the delivery layer here in exactly
the way it already is for config pushes (see routers/pushes.py). The
division is the same one used everywhere else in this codebase: the head
owns the decision and the record, the bot owns the conversation.

Two deliveries, tracked separately, because each can be lost on its own:

    /notifications  "there is an update, may we?"   -> notified_at
    /results        "here is how it went"           -> reported_at

Proposals are grouped by version rather than served one per node: a release
usually applies to the whole fleet, and asking an operator the same question
once per node would make approving five nodes five taps of the same answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.node import Node
from app.db.models.update import NodeUpdate, NodeUpdateStatus
from app.services import xray_updates

router = APIRouter(prefix="/api/v1/xray-updates", tags=["xray-updates"], dependencies=[ServiceAuth])


class UpdateNodeInfo(BaseModel):
    update_id: uuid.UUID
    node_id: uuid.UUID
    host: str
    country: str
    version_before: str | None


class UpdateGroup(BaseModel):
    """Every node that could move to the same target version."""

    target_version: str
    update_ids: list[uuid.UUID]
    nodes: list[UpdateNodeInfo]


class UpdateResult(BaseModel):
    update_id: uuid.UUID
    node_id: uuid.UUID
    host: str
    country: str
    target_version: str
    version_before: str | None
    version_after: str | None
    status: str
    error: str | None


class AckRequest(BaseModel):
    update_ids: list[uuid.UUID]


class DecideRequest(BaseModel):
    """Either a list of rows, or every pending row for one version.

    Both forms exist because the two callers ask differently: the admin
    panel points at rows it is displaying, while a Telegram button can only
    carry 64 bytes and so names the release instead.
    """

    update_ids: list[uuid.UUID] | None = None
    target_version: str | None = None
    approve: bool
    # Who authorised it, recorded on the row. The bot fills in the Telegram
    # chat that tapped the button.
    by: str = "telegram"


def _node_map(db: DbSession, rows: list[NodeUpdate]) -> dict[uuid.UUID, Node]:
    node_ids = {row.node_id for row in rows}
    if not node_ids:
        return {}
    return {node.id: node for node in db.scalars(select(Node).where(Node.id.in_(node_ids))).all()}


@router.get("/notifications", response_model=list[UpdateGroup])
def notifications(db: DbSession) -> list[UpdateGroup]:
    rows = db.scalars(
        select(NodeUpdate)
        .where(NodeUpdate.status == NodeUpdateStatus.pending, NodeUpdate.notified_at.is_(None))
        .order_by(NodeUpdate.created_at.asc())
    ).all()

    nodes = _node_map(db, list(rows))
    groups: dict[str, UpdateGroup] = {}
    for row in rows:
        node = nodes.get(row.node_id)
        if node is None:
            continue
        group = groups.setdefault(
            row.target_version,
            UpdateGroup(target_version=row.target_version, update_ids=[], nodes=[]),
        )
        group.update_ids.append(row.id)
        group.nodes.append(
            UpdateNodeInfo(
                update_id=row.id,
                node_id=node.id,
                host=node.host,
                country=node.country,
                version_before=row.version_before,
            )
        )
    return list(groups.values())


@router.post("/notifications/ack")
def ack_notifications(payload: AckRequest, db: DbSession) -> dict:
    return {"acked": _stamp(db, payload.update_ids, "notified_at")}


@router.get("/results", response_model=list[UpdateResult])
def results(db: DbSession, limit: int = 20) -> list[UpdateResult]:
    """Finished updates whose outcome has not been reported yet.

    Restricted to rows that were announced in the first place: an update
    approved in the admin panel is one the operator is already watching, and
    a Telegram message about it would be noise.
    """
    rows = db.scalars(
        select(NodeUpdate)
        .where(
            NodeUpdate.status.in_([NodeUpdateStatus.applied, NodeUpdateStatus.failed]),
            NodeUpdate.notified_at.is_not(None),
            NodeUpdate.reported_at.is_(None),
        )
        .order_by(NodeUpdate.finished_at.asc())
        .limit(limit)
    ).all()

    nodes = _node_map(db, list(rows))
    out = []
    for row in rows:
        node = nodes.get(row.node_id)
        out.append(
            UpdateResult(
                update_id=row.id,
                node_id=row.node_id,
                host=node.host if node else "?",
                country=node.country if node else "?",
                target_version=row.target_version,
                version_before=row.version_before,
                version_after=row.version_after,
                status=row.status.value,
                error=row.error,
            )
        )
    return out


@router.post("/results/ack")
def ack_results(payload: AckRequest, db: DbSession) -> dict:
    return {"acked": _stamp(db, payload.update_ids, "reported_at")}


@router.post("/decide")
def decide(payload: DecideRequest, db: DbSession) -> dict:
    if payload.target_version is not None:
        changed = xray_updates.decide_version(
            db, payload.target_version, approve=payload.approve, by=payload.by
        )
    elif payload.update_ids:
        changed = xray_updates.decide(
            db, payload.update_ids, approve=payload.approve, by=payload.by
        )
    else:
        raise HTTPException(
            status_code=422, detail="pass either update_ids or target_version"
        )
    db.commit()
    return {"changed": changed}


@router.post("/check")
def check(db: DbSession) -> dict:
    """Run a detection pass now, instead of waiting for the scheduled one."""
    raised = xray_updates.check_for_updates(db)
    db.commit()
    return {"raised": len(raised), "latest": xray_updates.latest_release_version()}


def _stamp(db: DbSession, update_ids: list[uuid.UUID], column: str) -> int:
    """Mark a delivery as done, ignoring rows that already carry the stamp.

    Idempotent on purpose: the bot acks after Telegram accepted the message,
    and a retry that arrives twice must not look like a second delivery.
    """
    rows = db.scalars(select(NodeUpdate).where(NodeUpdate.id.in_(update_ids))).all()
    now = datetime.now(UTC)
    stamped = 0
    for row in rows:
        if getattr(row, column) is None:
            setattr(row, column, now)
            stamped += 1
    db.commit()
    return stamped
