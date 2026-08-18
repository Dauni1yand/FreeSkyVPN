"""Trial, subscription status and payment confirmation — blueprint §09.

Tariffs live in the `plans` table rather than in code, so adding or
repricing one needs no deploy. The trial deliberately carries no `plan_id`:
its terms (7 days, one per account) change independently of the paid grid,
and tying it to a row there would mean editing a customer-facing plan to
change trial length.

Phase 3 records *what* a user is entitled to. Enforcing it — the speed cap
on free accounts — is phase 4, and lands in the Xray config rather than
here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.plan import (
    Payment,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionType,
)
from app.db.models.user import User
from app.services.timeutil import as_aware

logger = logging.getLogger(__name__)

TRIAL_DAYS = 7


class TrialAlreadyUsedError(RuntimeError):
    """One trial per account, ever."""


class UnknownPlanError(RuntimeError):
    """No active plan with that code."""


@dataclass(frozen=True)
class SubscriptionStatus:
    active: bool
    type: str | None  # "trial" | "paid" | None
    expires_at: datetime | None
    plan_code: str | None
    max_devices: int
    # Whether this account can still start a trial. Derived from
    # `users.trial_used_at`, not from the absence of a subscription: once a
    # trial expires the user has no active subscription either, and offering
    # them the trial again would only produce an error when they tap it.
    trial_available: bool


def current_subscription(db: Session, user: User) -> Subscription | None:
    """The user's live subscription, if any.

    Ordered by expiry so that a paid subscription bought while a trial is
    still running takes precedence — the user paid, they should get the
    longer entitlement rather than whichever row happens to come first.
    """
    now = datetime.now(UTC)
    subs = db.scalars(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.expires_at.desc())
    ).all()
    return next((s for s in subs if (as_aware(s.expires_at) or now) > now), None)


def status_for(db: Session, user: User) -> SubscriptionStatus:
    trial_available = user.trial_used_at is None
    sub = current_subscription(db, user)
    if sub is None:
        return SubscriptionStatus(
            active=False,
            type=None,
            expires_at=None,
            plan_code=None,
            max_devices=1,
            trial_available=trial_available,
        )

    plan = db.get(Plan, sub.plan_id) if sub.plan_id else None
    return SubscriptionStatus(
        active=True,
        type=sub.type.value,
        expires_at=as_aware(sub.expires_at),
        plan_code=plan.code if plan else None,
        max_devices=plan.max_devices if plan else 1,
        trial_available=trial_available,
    )


def start_trial(db: Session, user: User) -> SubscriptionStatus:
    if user.trial_used_at is not None:
        raise TrialAlreadyUsedError("this account has already used its trial")

    now = datetime.now(UTC)
    user.trial_used_at = now
    db.add(
        Subscription(
            user_id=user.id,
            plan_id=None,
            type=SubscriptionType.trial,
            expires_at=now + timedelta(days=TRIAL_DAYS),
        )
    )
    db.flush()
    return status_for(db, user)


def list_plans(db: Session) -> list[Plan]:
    return list(
        db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.duration_days.asc())).all()
    )


def confirm_payment(
    db: Session,
    user: User,
    plan_code: str,
    provider: str,
    provider_payment_id: str,
    amount: float,
    currency: str = "RUB",
) -> SubscriptionStatus:
    """Record a completed payment and extend the user's entitlement.

    Extends rather than replaces: a user who renews before expiry keeps the
    time they already paid for, instead of having it silently reset to
    "now + duration".
    """
    plan = db.scalar(select(Plan).where(Plan.code == plan_code, Plan.active.is_(True)))
    if plan is None:
        raise UnknownPlanError(f"no active plan with code {plan_code!r}")

    # Payment providers retry their notifications. Crediting the same payment
    # twice would hand out double the time paid for, so a repeat is a no-op
    # that simply reports the entitlement the first delivery already granted.
    already_recorded = db.scalar(
        select(Payment).where(Payment.provider == provider, Payment.external_id == provider_payment_id)
    )
    if already_recorded is not None:
        logger.info("payment %s/%s already recorded, ignoring repeat", provider, provider_payment_id)
        return status_for(db, user)

    db.add(
        Payment(
            user_id=user.id,
            provider=provider,
            external_id=provider_payment_id,
            amount=amount,
            currency=currency,
            status=PaymentStatus.succeeded,
        )
    )

    now = datetime.now(UTC)
    existing = current_subscription(db, user)
    # a running *paid* subscription is extended; a trial is superseded, since
    # paying should not merely queue behind the free days
    if existing is not None and existing.type == SubscriptionType.paid:
        base = as_aware(existing.expires_at) or now
        existing.plan_id = plan.id
        existing.expires_at = base + timedelta(days=plan.duration_days)
    else:
        db.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                type=SubscriptionType.paid,
                expires_at=now + timedelta(days=plan.duration_days),
            )
        )

    db.flush()
    return status_for(db, user)
