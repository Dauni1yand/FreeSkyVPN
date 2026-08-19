"""Configs for the head's own way out, and the report that moves it.

The proxy container that carries Telegram traffic asks here for a config
and says here when the one it has stopped working. Two endpoints rather
than a static setting, because a node that gets blocked is the normal case
this exists for — a proxy pinned to one node goes down with it, and takes
the bot and every alert with it.

Behind both is `connect_user` / `report_user_failure`, the same pair the
app and the bot use. The egress is a user; a node blocking it is the same
event as a node blocking a customer, and the head already knows how to
answer that by moving them.

Admin token only. This hands out a working config without any advertising
being watched, which is exactly what the app's token must never reach.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.auth import AdminAuth
from app.api.config_ops import (
    ConfigResponse,
    FailureResponse,
    connect_user,
    report_user_failure,
)
from app.api.deps import DbSession
from app.services import egress

router = APIRouter(prefix="/api/v1/egress", tags=["egress"], dependencies=[AdminAuth])


@router.post("/connect", response_model=ConfigResponse)
def connect(db: DbSession) -> ConfigResponse:
    """A config on whatever node is currently usable."""
    return connect_user(db, egress.get_or_create(db))


@router.post("/report-failure", response_model=FailureResponse)
def report_failure(db: DbSession) -> FailureResponse:
    """Say the current config is not carrying traffic, and get another.

    Counted against the inbound like any other report, so an inbound that
    is blocked for the egress contributes to the evidence that it is
    blocked for everyone.
    """
    return report_user_failure(db, egress.get_or_create(db))
