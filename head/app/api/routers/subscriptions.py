"""Plans, trial and payment confirmation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.auth import ServiceAuth
from app.api.deps import DbSession
from app.db.models.user import User
from app.services.config_selector import NoCapacityError
from app.services.subscriptions import (
    TrialAlreadyUsedError,
    UnknownPlanError,
    confirm_payment,
    list_plans,
    start_trial,
    status_for,
)
from app.services.tiering import reconcile_placement

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["subscriptions"], dependencies=[ServiceAuth])


class PlanResponse(BaseModel):
    code: str
    name: str
    duration_days: int
    max_devices: int
    price: float
    currency: str


class SubscriptionResponse(BaseModel):
    active: bool
    type: str | None
    expires_at: datetime | None
    plan_code: str | None
    max_devices: int
    trial_available: bool


class UserRequest(BaseModel):
    user_id: uuid.UUID


class PaymentConfirmRequest(BaseModel):
    user_id: uuid.UUID
    plan_code: str
    provider: str
    provider_payment_id: str
    amount: float
    currency: str = "RUB"


def _get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")
    return user


@router.get("/plans", response_model=list[PlanResponse])
def get_plans(db: DbSession) -> list[PlanResponse]:
    return [
        PlanResponse(
            code=p.code,
            name=p.name,
            duration_days=p.duration_days,
            max_devices=p.max_devices,
            price=float(p.price),
            currency=p.currency,
        )
        for p in list_plans(db)
    ]


@router.post("/subscription", response_model=SubscriptionResponse)
def get_subscription(payload: UserRequest, db: DbSession) -> SubscriptionResponse:
    user = _get_user(db, payload.user_id)
    return SubscriptionResponse(**status_for(db, user).__dict__)


@router.post("/subscription/trial", response_model=SubscriptionResponse)
def activate_trial(payload: UserRequest, db: DbSession) -> SubscriptionResponse:
    user = _get_user(db, payload.user_id)
    try:
        result = start_trial(db, user)
    except TrialAlreadyUsedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        logger.warning("trial user %s could not be placed on a paid node yet", user.id)

    db.commit()
    return SubscriptionResponse(**result.__dict__)


@router.post("/payments/confirm", response_model=SubscriptionResponse)
def payment_confirm(payload: PaymentConfirmRequest, db: DbSession) -> SubscriptionResponse:
    user = _get_user(db, payload.user_id)
    try:
        result = confirm_payment(
            db,
            user,
            plan_code=payload.plan_code,
            provider=payload.provider,
            provider_payment_id=payload.provider_payment_id,
            amount=payload.amount,
            currency=payload.currency,
        )
    except UnknownPlanError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # An upgrade should be felt now, not at the next reconnect. A failure to
    # find paid capacity must not fail the payment itself — the money is
    # taken and the entitlement recorded either way, and the periodic sweep
    # in scheduler.py will place them once capacity exists.
    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        logger.warning("paid user %s could not be placed on a paid node yet", user.id)

    db.commit()
    return SubscriptionResponse(**result.__dict__)
