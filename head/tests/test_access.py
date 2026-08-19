"""Access bought with attention.

This is the entire business model, so the tests that matter most are the
ones about not giving it away: a token cannot be spent twice, the fallback
cannot be farmed, and merging two accounts cannot manufacture time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.db.models.logs import AdView
from app.db.models.user import AdNonce
from app.services import access
from app.services.timeutil import as_aware
from tests.factories import make_user


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("AD_REWARD_MINUTES", "60")
    monkeypatch.setenv("ACCESS_GRACE_MINUTES", "15")
    monkeypatch.setenv("ACCESS_GRACE_INTERVAL_HOURS", "6")
    monkeypatch.setenv("ACCESS_MAX_HOURS", "24")
    monkeypatch.setenv("AD_SSV_REQUIRED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _watch(db, user):
    """One completed ad, start to finish."""
    nonce = access.issue_nonce(db, user)
    return access.redeem_nonce(db, user, nonce.nonce)


# --- the basic loop ------------------------------------------------------


def test_a_new_account_has_no_access(db):
    assert not access.has_access(make_user(db))


def test_watching_an_ad_buys_an_hour(db):
    user = make_user(db)

    state = _watch(db, user)

    assert state.active
    assert 3500 < state.seconds_remaining <= 3600
    assert not state.is_grace


def test_connect_is_refused_without_access(db):
    with pytest.raises(access.NoAccessError):
        access.require_access(make_user(db))


def test_access_lapses_when_the_hour_is_up(db):
    user = make_user(db)
    _watch(db, user)
    user.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert not access.has_access(user)


def test_a_second_ad_stacks_onto_what_is_left(db):
    """Someone who watches another ad with ten minutes left should end with
    seventy, not sixty — replacing would punish exactly the users the model
    depends on."""
    user = make_user(db)
    _watch(db, user)
    user.access_expires_at = datetime.now(UTC) + timedelta(minutes=10)

    state = _watch(db, user)

    assert 4100 < state.seconds_remaining <= 4200  # 70 minutes


def test_banked_access_is_capped(db):
    user = make_user(db)
    for _ in range(40):
        _watch(db, user)

    assert access.state_of(user).seconds_remaining <= 24 * 3600


def test_an_ad_never_shortens_access(db):
    """Regression: a plain min() against the ceiling would cut access back
    if the ceiling were ever lowered in configuration."""
    user = make_user(db)
    for _ in range(30):
        _watch(db, user)
    before = as_aware(user.access_expires_at)

    _watch(db, user)

    assert as_aware(user.access_expires_at) >= before


def test_every_grant_is_recorded(db):
    """A gap between ads watched and hours served should be answerable."""
    user = make_user(db)
    _watch(db, user)

    view = db.query(AdView).one()
    assert view.user_id == user.id
    assert view.reward_minutes == 60
    assert view.source == "rewarded_video"


# --- not giving it away --------------------------------------------------


def test_a_token_cannot_be_spent_twice(db):
    """Without this, one recorded HTTP call is an unlimited access generator."""
    user = make_user(db)
    nonce = access.issue_nonce(db, user)
    access.redeem_nonce(db, user, nonce.nonce)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, user, nonce.nonce)


def test_a_token_belongs_to_one_account(db):
    mine = make_user(db)
    theirs = make_user(db)
    nonce = access.issue_nonce(db, mine)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, theirs, nonce.nonce)


def test_an_expired_token_is_refused(db):
    user = make_user(db)
    nonce = access.issue_nonce(db, user)
    nonce.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, user, nonce.nonce)


def test_an_unknown_token_is_refused(db):
    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, make_user(db), "made-up")


def test_only_one_token_is_live_at_a_time(db):
    """Two would let a client bank tokens and redeem a stack at once."""
    user = make_user(db)
    first = access.issue_nonce(db, user)
    access.issue_nonce(db, user)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, user, first.nonce)


def test_the_client_is_not_believed_once_ssv_is_on(db, monkeypatch):
    monkeypatch.setenv("AD_SSV_REQUIRED", "true")
    get_settings.cache_clear()
    user = make_user(db)
    nonce = access.issue_nonce(db, user)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_nonce(db, user, nonce.nonce)


def test_the_network_callback_is_believed_even_then(db, monkeypatch):
    monkeypatch.setenv("AD_SSV_REQUIRED", "true")
    get_settings.cache_clear()
    user = make_user(db)
    nonce = access.issue_nonce(db, user)

    state = access.redeem_verified(db, nonce.nonce)

    assert state.active
    assert db.query(AdView).one().source == "ssv"


def test_the_network_callback_also_spends_the_token(db):
    user = make_user(db)
    nonce = access.issue_nonce(db, user)
    access.redeem_verified(db, nonce.nonce)

    with pytest.raises(access.InvalidNonceError):
        access.redeem_verified(db, nonce.nonce)


# --- the fallback --------------------------------------------------------


def test_grace_lets_someone_online_when_no_ad_could_be_shown(db):
    """A bad fill rate must not be a total outage: a VPN that will not
    connect is not a degraded VPN."""
    user = make_user(db)

    state = access.grant_grace(db, user)

    assert state.active
    assert state.is_grace
    assert state.seconds_remaining <= 15 * 60


def test_grace_cannot_be_farmed(db):
    """"The ad failed" is a claim the client makes about itself."""
    user = make_user(db)
    access.grant_grace(db, user)

    with pytest.raises(access.GraceUnavailableError):
        access.grant_grace(db, user)


def test_grace_returns_after_its_cooldown(db):
    user = make_user(db)
    access.grant_grace(db, user)
    user.grace_granted_at = datetime.now(UTC) - timedelta(hours=7)

    assert access.grant_grace(db, user).active


def test_grace_can_be_switched_off_entirely(db, monkeypatch):
    """For anyone who would rather fail closed than serve for free."""
    monkeypatch.setenv("ACCESS_GRACE_MINUTES", "0")
    get_settings.cache_clear()

    with pytest.raises(access.GraceUnavailableError):
        access.grant_grace(db, make_user(db))


def test_grace_does_not_demote_someone_already_on_earned_time(db):
    user = make_user(db)
    _watch(db, user)

    access.grant_grace(db, user)

    assert not access.state_of(user).is_grace


def test_a_real_ad_clears_the_grace_flag(db):
    user = make_user(db)
    access.grant_grace(db, user)

    assert _watch(db, user).is_grace is False


# --- manual grants -------------------------------------------------------


def test_a_manual_grant_records_who_made_it(db):
    """It bypasses the ads, so it has to be answerable."""
    user = make_user(db)

    access.grant_manual(db, user, 120, by="operator")

    assert access.has_access(user)
    assert db.query(AdView).one().source == "manual:operator"


def test_revoking_ends_access_immediately(db):
    user = make_user(db)
    _watch(db, user)

    access.revoke(db, user)

    assert not access.has_access(user)
    assert not access.state_of(user).is_grace


def test_issuing_a_token_does_not_grant_anything(db):
    """Asking to watch an ad is not watching one."""
    user = make_user(db)
    access.issue_nonce(db, user)

    assert not access.has_access(user)
    assert db.query(AdNonce).count() == 1
    assert db.query(AdView).count() == 0
