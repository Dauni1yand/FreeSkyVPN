from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.plan import Payment, Plan, Subscription, SubscriptionType
from app.services.subscriptions import (
    TRIAL_DAYS,
    TrialAlreadyUsedError,
    UnknownPlanError,
    confirm_payment,
    start_trial,
    status_for,
)
from tests.factories import make_user


@pytest.fixture
def plan(db) -> Plan:
    plan = Plan(code="month", name="1 месяц", duration_days=30, max_devices=3, price=199)
    db.add(plan)
    db.flush()
    return plan


def test_new_user_has_no_subscription(db):
    user = make_user(db)
    result = status_for(db, user)
    assert result.active is False
    assert result.type is None


def test_trial_grants_seven_days(db):
    user = make_user(db)
    result = start_trial(db, user)

    assert result.active is True
    assert result.type == "trial"
    remaining = result.expires_at - datetime.now(UTC)
    assert timedelta(days=TRIAL_DAYS - 1) < remaining <= timedelta(days=TRIAL_DAYS)


def test_trial_is_once_per_account(db):
    user = make_user(db)
    start_trial(db, user)

    with pytest.raises(TrialAlreadyUsedError):
        start_trial(db, user)


def test_payment_creates_a_paid_subscription(db, plan):
    user = make_user(db)

    result = confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
    )

    assert result.active is True
    assert result.type == "paid"
    assert result.plan_code == "month"
    assert result.max_devices == 3
    assert db.query(Payment).count() == 1


def test_repeated_payment_notification_does_not_double_credit(db, plan):
    """Payment providers retry; the same charge must not buy two months."""
    user = make_user(db)

    first = confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
    )
    second = confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
    )

    assert first.expires_at == second.expires_at
    assert db.query(Payment).count() == 1
    assert db.query(Subscription).count() == 1


def test_renewal_extends_rather_than_resets(db, plan):
    """A user who renews early keeps the time they already paid for."""
    user = make_user(db)
    confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
    )
    after_first = status_for(db, user).expires_at

    confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_2", amount=199
    )
    after_second = status_for(db, user).expires_at

    assert after_second - after_first == timedelta(days=30)
    assert db.query(Subscription).count() == 1, "renewal extends the existing row"


def test_paying_during_a_trial_supersedes_it(db, plan):
    """Paying must not merely queue behind the remaining free days."""
    user = make_user(db)
    start_trial(db, user)

    result = confirm_payment(
        db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
    )

    assert result.type == "paid"
    remaining = result.expires_at - datetime.now(UTC)
    assert remaining > timedelta(days=29), "the paid month starts now, not after the trial"


def test_expired_trial_is_not_offered_again(db):
    """An expired trial leaves no active subscription, but must not look like
    a fresh account — offering the button again would only error on tap."""
    user = make_user(db)
    assert status_for(db, user).trial_available is True

    start_trial(db, user)
    sub = db.query(Subscription).one()
    sub.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    result = status_for(db, user)
    assert result.active is False
    assert result.trial_available is False


def test_expired_subscription_is_not_active(db, plan):
    user = make_user(db)
    db.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            type=SubscriptionType.paid,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db.flush()

    assert status_for(db, user).active is False


def test_unknown_plan_is_rejected(db):
    user = make_user(db)
    with pytest.raises(UnknownPlanError):
        confirm_payment(
            db, user, plan_code="nope", provider="telegram", provider_payment_id="ch_1", amount=1
        )


def test_inactive_plans_cannot_be_purchased(db, plan):
    plan.active = False
    db.flush()
    user = make_user(db)

    with pytest.raises(UnknownPlanError):
        confirm_payment(
            db, user, plan_code="month", provider="telegram", provider_payment_id="ch_1", amount=199
        )
