"""Outbox drain — how a decided config change reaches the user.

The head decides who needs a new config (fail_handler declares an inbound
dead for everyone on it), but it cannot reach users itself. The delivery
layer polls here: the Telegram bot in phase 3, push notifications in the
Android phase. Rows stay pending until acknowledged, so a bot restart
mid-delivery loses nothing.

Each pending row carries the rendered `vless://` for the user's *current*
assignment rather than a stored snapshot — if several changes queued up
for one user, the link they receive is the one that is actually live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.node import Assignment, Inbound, Node
from app.db.models.outbox import ConfigPush
from app.db.models.user import AuthIdentity, AuthProvider
from app.services.vless_link import build_vless_link

router = APIRouter(prefix="/api/v1/pushes", tags=["pushes"], dependencies=[ServiceAuth])


class PendingPush(BaseModel):
    push_id: uuid.UUID
    user_id: uuid.UUID
    telegram_id: str | None
    reason: str
    vless_url: str | None
    node_country: str | None


class AckRequest(BaseModel):
    push_id: uuid.UUID
    error: str | None = None


@router.get("/pending", response_model=list[PendingPush])
def pending(db: DbSession, limit: int = 50) -> list[PendingPush]:
    rows = db.scalars(
        select(ConfigPush)
        .where(ConfigPush.delivered_at.is_(None))
        .order_by(ConfigPush.created_at.asc())
        .limit(limit)
    ).all()

    result = []
    for push in rows:
        identity = db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.user_id == push.user_id,
                AuthIdentity.provider == AuthProvider.telegram,
            )
        )

        assignment = db.scalar(
            select(Assignment)
            .where(Assignment.user_id == push.user_id, Assignment.released_at.is_(None))
            .order_by(Assignment.assigned_at.desc())
        )
        vless_url = None
        node_country = None
        if assignment is not None:
            inbound = db.get(Inbound, assignment.inbound_id)
            node = db.get(Node, inbound.node_id)
            vless_url = build_vless_link(node, inbound, assignment.xray_uuid)
            node_country = node.country

        result.append(
            PendingPush(
                push_id=push.id,
                user_id=push.user_id,
                telegram_id=identity.provider_uid if identity else None,
                reason=push.reason.value,
                vless_url=vless_url,
                node_country=node_country,
            )
        )
    return result


@router.post("/ack")
def ack(payload: AckRequest, db: DbSession) -> dict:
    push = db.get(ConfigPush, payload.push_id)
    if push is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown push")

    push.delivered_at = datetime.now(UTC)
    push.delivery_error = payload.error
    db.commit()
    return {"ok": True}
