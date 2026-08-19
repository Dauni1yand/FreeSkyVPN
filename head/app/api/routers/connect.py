"""The two buttons that matter, for callers that name a user by id.

Used by the bot, which knows a Telegram account's `user_id` but holds no
bearer token for it. The app uses `/api/v1/me/*` instead and is identified
by its own token.

Both doors lead to the same two functions in app/api/config_ops.py. They
used to be implemented twice, which is how one of them ended up without the
check that decides whether the user has paid for the hour.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.auth import AdminAuth
from app.api.config_ops import (
    ConfigResponse,
    FailureResponse,
    connect_user,
    report_user_failure,
)
from app.api.deps import DbSession
from app.db.models.user import User

router = APIRouter(prefix="/api/v1", tags=["config"], dependencies=[AdminAuth])


class ConnectRequest(BaseModel):
    user_id: uuid.UUID


def _get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")
    return user


@router.post("/connect", response_model=ConfigResponse)
def connect(payload: ConnectRequest, db: DbSession) -> ConfigResponse:
    return connect_user(db, _get_user(db, payload.user_id))


@router.post("/report-failure", response_model=FailureResponse)
def report_not_working(payload: ConnectRequest, db: DbSession) -> FailureResponse:
    return report_user_failure(db, _get_user(db, payload.user_id))
