"""The two buttons that matter — blueprint §07.

`/connect` is the single connect button: no country, no server list.
`/report-failure` is the "не работает" button.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.user import User
from app.services.config_selector import NoCapacityError, assign_config
from app.services.fail_handler import (
    NoActiveConfigError,
    ReportTooSoonError,
    report_failure,
)

router = APIRouter(prefix="/api/v1", tags=["config"], dependencies=[ServiceAuth])


class ConnectRequest(BaseModel):
    user_id: uuid.UUID


class ConfigResponse(BaseModel):
    vless_url: str
    node_country: str
    inbound_id: str


class FailureResponse(ConfigResponse):
    inbound_declared_dead: bool
    node_declared_burned: bool
    users_migrated: int


def _get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")
    return user


@router.post("/connect", response_model=ConfigResponse)
def connect(payload: ConnectRequest, db: DbSession) -> ConfigResponse:
    user = _get_user(db, payload.user_id)
    try:
        config = assign_config(db, user)
    except NoCapacityError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    db.commit()
    return ConfigResponse(
        vless_url=config.vless_url, node_country=config.node_country, inbound_id=config.inbound_id
    )


@router.post("/report-failure", response_model=FailureResponse)
def report_not_working(payload: ConnectRequest, db: DbSession) -> FailureResponse:
    user = _get_user(db, payload.user_id)
    try:
        outcome = report_failure(db, user)
    except ReportTooSoonError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except NoActiveConfigError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NoCapacityError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    db.commit()
    return FailureResponse(
        vless_url=outcome.config.vless_url,
        node_country=outcome.config.node_country,
        inbound_id=outcome.config.inbound_id,
        inbound_declared_dead=outcome.inbound_declared_dead,
        node_declared_burned=outcome.node_declared_burned,
        users_migrated=outcome.users_migrated,
    )
