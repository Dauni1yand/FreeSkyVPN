"""The two operations that hand out a working config, in one place.

They exist behind two doors — `/api/v1/connect` for the bot, which names a
user by id, and `/api/v1/me/connect` for the app, which is identified by a
bearer token — and for a while they were implemented twice. That is how the
access check came to be on one and not the other: the app could not connect
without paying for the hour, while the same operation one route over handed
out a config to anybody holding the service token.

Which was everybody. The service token ships inside the APK; unzipping one
yields it. So the bypass was: take the token, create an account through
`/auth/device`, call `/connect`, and use the service indefinitely without
seeing a single advertisement.

Hence this module. Not because duplication is untidy, but because these two
paths drifted apart on precisely the check that the business runs on, and
the only durable fix is that there is one copy to change.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.services import access
from app.services.config_selector import NoCapacityError, assign_config
from app.services.fail_handler import (
    NoActiveConfigError,
    ReportTooSoonError,
    report_failure,
)


class ConfigResponse(BaseModel):
    vless_url: str
    node_country: str
    inbound_id: str


class FailureResponse(ConfigResponse):
    inbound_declared_dead: bool
    node_declared_burned: bool
    users_migrated: int


def connect_user(db: Session, user: User) -> ConfigResponse:
    """Hand this user a config, if they have bought the time for one."""
    _require_paid_time(user)

    try:
        config = assign_config(db, user)
    except NoCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    db.commit()
    return ConfigResponse(
        vless_url=config.vless_url,
        node_country=config.node_country,
        inbound_id=config.inbound_id,
    )


def report_user_failure(db: Session, user: User) -> FailureResponse:
    """The "не работает" button: swap the config, and tell us the old one broke."""
    _require_paid_time(user)

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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    db.commit()
    return FailureResponse(
        vless_url=outcome.config.vless_url,
        node_country=outcome.config.node_country,
        inbound_id=outcome.config.inbound_id,
        inbound_declared_dead=outcome.inbound_declared_dead,
        node_declared_burned=outcome.node_declared_burned,
        users_migrated=outcome.users_migrated,
    )


def _require_paid_time(user: User) -> None:
    """402 rather than 403.

    The client's move is to show an advertisement, not to re-authenticate,
    and a client that retries on 401/403 must not confuse the two.
    """
    try:
        access.require_access(user)
    except access.NoAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
        ) from exc
