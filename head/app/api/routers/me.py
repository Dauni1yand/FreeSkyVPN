"""Everything the Android app can do, scoped to whoever holds the token.

The distinction from the routers next door is the whole point: those take a
`user_id` in the body and trust the service token, which is safe when the
caller is our own bot on our own server. Here the user is derived from the
bearer token and a `user_id` in the body would be ignored — a client that
can name any user is a client that can act as any user.

The endpoints mirror the four buttons the product has: connect, "не
работает", subscription, account.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.api.user_auth_dep import CurrentUser
from app.config import get_settings
from app.db.models.user import AuthIdentity, AuthProvider
from app.services import routing_policy, user_auth
from app.services.config_selector import NoCapacityError, assign_config
from app.services.fail_handler import (
    NoActiveConfigError,
    ReportTooSoonError,
    report_failure,
)
from app.services.subscriptions import TrialAlreadyUsedError, start_trial, status_for
from app.services.tiering import reconcile_placement

# The service token still guards the whole router: it says "this is our app
# talking", while the bearer token says "and this is who it is talking for".
# Dropping the first would leave the registration endpoint open to anyone on
# the internet to spin up accounts.
router = APIRouter(prefix="/api/v1", tags=["client"], dependencies=[ServiceAuth])


# --- registration --------------------------------------------------------


class RegisterRequest(BaseModel):
    # Cosmetic, shown in the admin panel. Never trusted for anything.
    device_label: str | None = None


class RegisterResponse(BaseModel):
    user_id: uuid.UUID
    token: str
    session_id: uuid.UUID


@router.post("/auth/device", response_model=RegisterResponse, status_code=201)
def register_device(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Create an anonymous account. Called once, on first launch.

    The token comes back exactly once and is never retrievable again — if
    the app loses it, that install has lost the account, which is the price
    of not asking anyone to register before their first connection.
    """
    issued = user_auth.register_device(db, device_label=payload.device_label)
    db.commit()
    return RegisterResponse(
        user_id=issued.user_id, token=issued.token, session_id=issued.session_id
    )


# --- the connect button --------------------------------------------------


class ConfigResponse(BaseModel):
    vless_url: str
    node_country: str
    inbound_id: str


class FailureResponse(ConfigResponse):
    inbound_declared_dead: bool
    node_declared_burned: bool
    users_migrated: int


@router.post("/me/connect", response_model=ConfigResponse)
def connect(db: DbSession, user: CurrentUser) -> ConfigResponse:
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


@router.post("/me/report-failure", response_model=FailureResponse)
def report_not_working(db: DbSession, user: CurrentUser) -> FailureResponse:
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


# --- account and subscription -------------------------------------------


class AccountResponse(BaseModel):
    user_id: uuid.UUID
    telegram_linked: bool
    subscription_active: bool
    subscription_type: str | None
    expires_at: datetime | None
    trial_available: bool
    # False while PAYMENT_PROVIDER_TOKEN is unset, so the app hides the buy
    # button instead of offering a purchase that cannot complete.
    payments_available: bool


def _account(db: DbSession, user) -> AccountResponse:
    sub = status_for(db, user)
    linked = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.provider == AuthProvider.telegram,
        )
    )
    return AccountResponse(
        user_id=user.id,
        telegram_linked=linked is not None,
        subscription_active=sub.active,
        subscription_type=sub.type,
        expires_at=sub.expires_at,
        trial_available=sub.trial_available,
        payments_available=bool(get_settings().payment_provider_token),
    )


@router.get("/me", response_model=AccountResponse)
def me(db: DbSession, user: CurrentUser) -> AccountResponse:
    return _account(db, user)


@router.post("/me/trial", response_model=AccountResponse)
def trial(db: DbSession, user: CurrentUser) -> AccountResponse:
    try:
        start_trial(db, user)
    except TrialAlreadyUsedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # A trial changes the tier, and the tier changes which ports the user
    # belongs on. Moving them now rather than at the next sweep is what makes
    # the upgrade feel immediate.
    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        # The entitlement is real even if no paid-tier slot is free this
        # second; the reconciliation loop will move them.
        pass

    db.commit()
    return _account(db, user)


# --- linking to Telegram -------------------------------------------------


class LinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    bot_username: str | None


@router.post("/me/link/start", response_model=LinkCodeResponse)
def link_start(db: DbSession, user: CurrentUser) -> LinkCodeResponse:
    """Hand the app a code to show. The user types it to the bot."""
    link = user_auth.start_link(db, user)
    db.commit()
    return LinkCodeResponse(
        code=link.code,
        expires_at=link.expires_at,
        bot_username=get_settings().telegram_bot_username or None,
    )


class RedeemRequest(BaseModel):
    code: str
    telegram_id: str


class RedeemResponse(BaseModel):
    user_id: uuid.UUID


@router.post("/auth/link/redeem", response_model=RedeemResponse)
def link_redeem(payload: RedeemRequest, db: DbSession) -> RedeemResponse:
    """Called by the bot, never by the app.

    Deliberately service-token-only and *not* bearer-authenticated: the
    caller here is the bot vouching for a Telegram id it saw a message
    arrive from. An app holding a bearer token cannot claim a Telegram
    identity for itself.
    """
    try:
        survivor = user_auth.redeem_link(db, payload.code, payload.telegram_id)
    except user_auth.InvalidLinkCodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()
    return RedeemResponse(user_id=survivor.id)


# --- split tunnelling ----------------------------------------------------


class RoutingPolicyResponse(BaseModel):
    version: int
    direct_tlds: list[str]
    direct_domains: list[str]
    direct_packages: list[str]
    direct_geoip: list[str]


@router.get("/routing-policy", response_model=RoutingPolicyResponse)
def get_routing_policy(_user: CurrentUser) -> RoutingPolicyResponse:
    """What the app should keep outside the tunnel.

    Served rather than compiled in so a service that starts misbehaving can
    be moved to the direct list without waiting for a store review. The app
    ships the same content as a fallback for first launch and for whenever
    the head cannot be reached.
    """
    policy = routing_policy.current_policy()
    return RoutingPolicyResponse(
        version=policy.version,
        direct_tlds=list(policy.direct_tlds),
        direct_domains=list(policy.direct_domains),
        direct_packages=list(policy.direct_packages),
        direct_geoip=list(policy.direct_geoip),
    )
