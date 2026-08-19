"""Everything the Android app can do, scoped to whoever holds the token.

The distinction from the routers next door is the whole point: those take a
`user_id` in the body and trust the service token, which is safe when the
caller is our own bot on our own server. Here the user is derived from the
bearer token and a `user_id` in the body would be ignored — a client that
can name any user is a client that can act as any user.

The endpoints mirror the four buttons the product has: connect, "не
работает", доступ, аккаунт.
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
from app.db.models.user import AuthIdentity, AuthProvider, User
from app.services import access, routing_policy, user_auth
from app.services.config_selector import NoCapacityError, assign_config
from app.services.fail_handler import (
    NoActiveConfigError,
    ReportTooSoonError,
    report_failure,
)
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
    # The whole business model in one guard: the service is funded by
    # advertising, so an hour has to be bought before it can be used.
    # 402 rather than 403 — the client's move is to show an ad, not to
    # re-authenticate, and the two must not look alike to it.
    try:
        access.require_access(user)
    except access.NoAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
        ) from exc

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


# --- account and access -------------------------------------------------


class AccountResponse(BaseModel):
    user_id: uuid.UUID
    telegram_linked: bool
    # How much of the hour bought with the last ad is left. The app turns
    # this into a countdown and into whether the connect button works.
    access_active: bool
    access_expires_at: datetime | None
    access_seconds_remaining: int
    #: True when the current stretch came from the fallback rather than an
    #: ad, so the app can say why it feels slower.
    access_is_grace: bool
    #: What the user can buy at the connect button. Served rather than
    #: compiled into the app so the offer can change without a release.
    packages: list[PackageOption]


def _account(db: DbSession, user) -> AccountResponse:
    state = access.state_of(user)
    linked = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.provider == AuthProvider.telegram,
        )
    )
    return AccountResponse(
        user_id=user.id,
        telegram_linked=linked is not None,
        access_active=state.active,
        access_expires_at=state.expires_at,
        access_seconds_remaining=state.seconds_remaining,
        access_is_grace=state.is_grace,
        packages=[
            PackageOption(
                code=p.code,
                label=p.label,
                ad_kind=p.kind.value,
                views=p.views,
                total_minutes=p.total_minutes,
            )
            for p in access.PACKAGES.values()
        ],
    )


@router.get("/me", response_model=AccountResponse)
def me(db: DbSession, user: CurrentUser) -> AccountResponse:
    return _account(db, user)


# --- buying an hour with attention --------------------------------------


class PackageOption(BaseModel):
    """One thing the user can buy at the connect button."""

    code: str
    label: str
    #: "rewarded" must be watched through; "interstitial" is skippable.
    ad_kind: str
    views: int
    total_minutes: int


class AdTicket(BaseModel):
    nonce: str
    package: str
    ad_kind: str
    views_required: int
    minutes_per_view: int


class AdPrepareRequest(BaseModel):
    package: str = access.DEFAULT_PACKAGE


@router.post("/me/ad/prepare", response_model=AdTicket)
def ad_prepare(payload: AdPrepareRequest, db: DbSession, user: CurrentUser) -> AdTicket:
    """Issue the token covering one run through a package's ads.

    The package is validated here and stored on the token, so what a view is
    worth stays a server-side decision. A client able to name its own reward
    would name a large one.

    The token is friction rather than proof — a modified client can ask for
    one and claim the views without showing anything. The real control is
    the network's own callback, see /ad/verify.
    """
    try:
        package = access.package_for(payload.package)
    except access.UnknownPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    nonce = access.issue_nonce(db, user, package.code)
    db.commit()
    return AdTicket(
        nonce=nonce.nonce,
        package=package.code,
        ad_kind=package.kind.value,
        views_required=package.views,
        minutes_per_view=package.minutes_per_view,
    )


class AdCompleteRequest(BaseModel):
    nonce: str


class AdProgress(BaseModel):
    views_done: int
    views_required: int
    minutes_granted: int
    #: False while the package still owes the user another video.
    complete: bool
    account: AccountResponse


@router.post("/me/ad/complete", response_model=AdProgress)
def ad_complete(payload: AdCompleteRequest, db: DbSession, user: CurrentUser) -> AdProgress:
    """Credit one completed view.

    Time is granted per view, not per package: someone who watches the first
    of two ads and closes the app keeps the hour they earned. Taking the
    view and giving nothing would be the one behaviour guaranteed to make
    people stop watching.
    """
    try:
        result = access.redeem_nonce(db, user, payload.nonce)
    except access.InvalidNonceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _settle(db, user)
    db.commit()
    return AdProgress(
        views_done=result.views_done,
        views_required=result.views_required,
        minutes_granted=result.minutes_granted,
        complete=result.complete,
        account=_account(db, user),
    )


@router.post("/me/ad/unavailable", response_model=AccountResponse)
def ad_unavailable(db: DbSession, user: CurrentUser) -> AccountResponse:
    """The client could not get an ad to show. Let them online anyway, briefly.

    Without this, a bad fill rate or an outage at the ad network is a total
    outage of the VPN — and a VPN that will not connect is not a degraded
    VPN. The grant is short, rate limited and lands on the lower-priority
    class, so it costs less than the ad it replaces and cannot become the
    way to skip one.
    """
    try:
        access.grant_grace(db, user)
    except access.GraceUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    _settle(db, user)
    db.commit()
    return _account(db, user)


def _settle(db: DbSession, user) -> None:
    """Move the user onto the class they just earned, if they are connected.

    Doing it now rather than at the next sweep is what makes the reward feel
    immediate. A user with no live assignment is left alone — they will be
    placed correctly when they connect.
    """
    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        # The access is real even if no slot in that class is free this
        # second; the reconciliation loop will move them.
        pass


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


# --- server-side verification -------------------------------------------


class AdVerifyRequest(BaseModel):
    nonce: str


@router.post("/ad/verify")
def ad_verify(payload: AdVerifyRequest, db: DbSession) -> dict:
    """Grant against the ad network's server-to-server callback.

    Deliberately not bearer-authenticated: the caller is the network, which
    has no user token and no device to lie for. This is the only path that
    actually proves an ad was watched — everything on /me/ad/* trusts the
    client. Wire this up, set AD_SSV_REQUIRED, and the client's word stops
    being accepted.

    The network's own signature check belongs in front of this endpoint
    (each provider signs differently); the shared service token is what
    keeps it from being open to the internet in the meantime.
    """
    try:
        result = access.redeem_verified(db, payload.nonce)
    except access.InvalidNonceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()
    return {
        "granted_until": result.state.expires_at.isoformat() if result.state.expires_at else None,
        "views_done": result.views_done,
        "views_required": result.views_required,
    }


# --- service-side grants -------------------------------------------------


class GrantAccessRequest(BaseModel):
    user_id: uuid.UUID
    minutes: int | None = None


@router.post("/admin/grant-access", response_model=AccountResponse)
def grant_access(payload: GrantAccessRequest, db: DbSession) -> AccountResponse:
    """Put an account online without an ad. Service-token only.

    Exists for the bot, which cannot show rewarded video — Telegram has no
    such SDK — and therefore cannot take part in what pays for the servers.
    The bot decides who is allowed to ask; this records that it happened,
    with the source marked, so a gap between ads watched and hours served
    stays answerable.
    """
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")

    minutes = payload.minutes or get_settings().ad_reward_minutes
    access.grant_manual(db, user, minutes, by="bot")

    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        pass

    db.commit()
    return _account(db, user)
