"""Per-user tokens and account linking.

This is the layer that stops an APK from being a master key, so the tests
that matter most are the negative ones: a token must not authenticate
anyone but its own account, and merging must not be a way to launder a ban
or farm free trials.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.user import (
    AuthIdentity,
    AuthProvider,
    ClientType,
    LinkCode,
    User,
    UserSession,
    UserStatus,
)
from app.services import user_auth
from tests.factories import make_user

# --- registration and tokens --------------------------------------------


def test_registration_creates_an_account_and_a_working_token(db):
    issued = user_auth.register_device(db, device_label="Pixel 7 / Android 14")
    db.commit()

    user = user_auth.authenticate(db, issued.token)
    assert user.id == issued.user_id


def test_two_registrations_are_two_separate_accounts(db):
    first = user_auth.register_device(db)
    second = user_auth.register_device(db)
    db.commit()

    assert first.user_id != second.user_id
    assert first.token != second.token


def test_one_token_does_not_open_another_account(db):
    """The whole reason this layer exists: with only a service token, any
    client could name any user_id and be served."""
    mine = user_auth.register_device(db)
    theirs = user_auth.register_device(db)
    db.commit()

    assert user_auth.authenticate(db, mine.token).id == mine.user_id
    assert user_auth.authenticate(db, theirs.token).id == theirs.user_id


def test_the_plaintext_token_is_never_stored(db):
    """A database dump must not yield working tokens."""
    issued = user_auth.register_device(db)
    db.commit()

    session = db.query(UserSession).one()
    assert session.token_hash is not None
    assert issued.token not in session.token_hash
    assert session.token_hash == user_auth.hash_token(issued.token)


def test_an_unknown_token_is_rejected(db):
    with pytest.raises(user_auth.AuthError):
        user_auth.authenticate(db, "fsv1_completely-made-up")


def test_an_empty_token_is_rejected(db):
    with pytest.raises(user_auth.AuthError):
        user_auth.authenticate(db, "")


def test_a_revoked_token_stops_working(db):
    issued = user_auth.register_device(db)
    db.commit()

    assert user_auth.revoke_session(db, issued.session_id)
    with pytest.raises(user_auth.AuthError):
        user_auth.authenticate(db, issued.token)


def test_revoking_one_device_leaves_the_others_alone(db):
    issued = user_auth.register_device(db)
    user = user_auth.authenticate(db, issued.token)
    second = user_auth.issue_session(db, user, client_type=ClientType.android)
    db.commit()

    user_auth.revoke_session(db, issued.session_id)

    assert user_auth.authenticate(db, second.token).id == user.id


def test_a_banned_account_is_refused_distinctly(db):
    """403, not 401: the token is fine and retrying will not help, so a
    client that refreshes on 401 must not spin."""
    issued = user_auth.register_device(db)
    db.commit()
    user = db.get(User, issued.user_id)
    user.status = UserStatus.banned
    db.commit()

    with pytest.raises(user_auth.BannedError):
        user_auth.authenticate(db, issued.token)


def test_liveness_is_not_written_on_every_request(db):
    """An app polling its state must not turn every read into a write."""
    issued = user_auth.register_device(db)
    db.commit()
    session = db.query(UserSession).one()
    session.last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    before = session.last_seen_at

    user_auth.authenticate(db, issued.token)

    from app.services.timeutil import as_aware

    assert as_aware(db.query(UserSession).one().last_seen_at) == as_aware(before)


def test_liveness_is_written_once_it_is_stale(db):
    issued = user_auth.register_device(db)
    db.commit()
    session = db.query(UserSession).one()
    session.last_seen_at = datetime.now(UTC) - timedelta(hours=5)
    db.commit()
    before = session.last_seen_at

    user_auth.authenticate(db, issued.token)

    from app.services.timeutil import as_aware

    assert as_aware(db.query(UserSession).one().last_seen_at) > as_aware(before)


# --- link codes ----------------------------------------------------------


def test_a_link_code_attaches_telegram_to_the_app_account(db):
    issued = user_auth.register_device(db)
    db.commit()
    user = db.get(User, issued.user_id)

    link = user_auth.start_link(db, user)
    db.commit()
    survivor = user_auth.redeem_link(db, link.code, "555")
    db.commit()

    assert survivor.id == user.id
    identity = db.query(AuthIdentity).filter_by(provider=AuthProvider.telegram).one()
    assert identity.provider_uid == "555"
    assert identity.user_id == user.id


def test_a_code_works_only_once(db):
    user = make_user(db)
    link = user_auth.start_link(db, user)
    db.commit()
    user_auth.redeem_link(db, link.code, "555")
    db.commit()

    with pytest.raises(user_auth.InvalidLinkCodeError):
        user_auth.redeem_link(db, link.code, "666")


def test_an_expired_code_is_refused(db):
    user = make_user(db)
    link = user_auth.start_link(db, user)
    link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(user_auth.InvalidLinkCodeError):
        user_auth.redeem_link(db, link.code, "555")


def test_an_unknown_code_is_refused(db):
    with pytest.raises(user_auth.InvalidLinkCodeError):
        user_auth.redeem_link(db, "000000", "555")


def test_requesting_a_new_code_retires_the_previous_one(db):
    """Two live codes for one account is a support conversation waiting to
    happen — the screen the user is looking at must be the one that works."""
    user = make_user(db)
    first = user_auth.start_link(db, user)
    db.commit()
    first_code = first.code

    second = user_auth.start_link(db, user)
    db.commit()

    with pytest.raises(user_auth.InvalidLinkCodeError):
        user_auth.redeem_link(db, first_code, "555")
    assert user_auth.redeem_link(db, second.code, "555").id == user.id


def test_relinking_the_same_telegram_account_is_harmless(db):
    user = make_user(db)
    user_auth.redeem_link(db, user_auth.start_link(db, user).code, "555")
    db.commit()

    again = user_auth.redeem_link(db, user_auth.start_link(db, user).code, "555")
    db.commit()

    assert again.id == user.id
    assert db.query(AuthIdentity).filter_by(provider=AuthProvider.telegram).count() == 1


# --- merging -------------------------------------------------------------


def test_linking_to_an_existing_telegram_account_merges_the_two(db):
    bot_user = make_user(db)
    db.add(
        AuthIdentity(user_id=bot_user.id, provider=AuthProvider.telegram, provider_uid="555")
    )
    app_issued = user_auth.register_device(db)
    db.commit()
    app_user = db.get(User, app_issued.user_id)

    survivor = user_auth.redeem_link(db, user_auth.start_link(db, app_user).code, "555")
    db.commit()

    assert survivor.id == bot_user.id
    assert db.get(User, app_issued.user_id) is None
    # The app's token must keep working — the user did not lose their device.
    assert user_auth.authenticate(db, app_issued.token).id == bot_user.id


def test_a_merge_carries_the_bought_access_across(db):
    """Losing an hour someone watched an ad for, because they tapped "link
    account", would be the worst possible outcome of a convenience feature."""
    bot_user = make_user(db)
    db.add(
        AuthIdentity(user_id=bot_user.id, provider=AuthProvider.telegram, provider_uid="555")
    )
    app_user = make_user(db)
    expires = datetime.now(UTC) + timedelta(hours=3)
    app_user.access_expires_at = expires
    db.commit()

    survivor = user_auth.redeem_link(db, user_auth.start_link(db, app_user).code, "555")
    db.commit()

    from app.services.timeutil import as_aware

    assert as_aware(survivor.access_expires_at) == expires


def test_merging_cannot_hand_back_a_used_fallback(db):
    """Otherwise "link account" becomes a way to reset the grace cooldown."""
    keep = make_user(db)
    absorb = make_user(db)
    absorb.grace_granted_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()

    survivor = user_auth.merge_accounts(db, keep=keep, absorb=absorb)
    db.commit()

    assert survivor.grace_granted_at is not None


def test_merging_keeps_the_later_grace_use(db):
    from app.services.timeutil import as_aware

    older = datetime.now(UTC) - timedelta(hours=20)
    newer = datetime.now(UTC) - timedelta(hours=1)
    keep = make_user(db)
    keep.grace_granted_at = older
    absorb = make_user(db)
    absorb.grace_granted_at = newer
    db.commit()

    survivor = user_auth.merge_accounts(db, keep=keep, absorb=absorb)
    db.commit()

    assert as_aware(survivor.grace_granted_at) == newer


def test_merging_keeps_the_later_access_expiry(db):
    """Both stretches were paid for; the merged account keeps the longer."""
    from app.services.timeutil import as_aware

    soon = datetime.now(UTC) + timedelta(minutes=20)
    later = datetime.now(UTC) + timedelta(hours=5)
    keep = make_user(db)
    keep.access_expires_at = soon
    absorb = make_user(db)
    absorb.access_expires_at = later
    db.commit()

    survivor = user_auth.merge_accounts(db, keep=keep, absorb=absorb)
    db.commit()

    assert as_aware(survivor.access_expires_at) == later


def test_a_ban_survives_a_merge(db):
    """A ban must not be shakeable by merging into a clean account."""
    keep = make_user(db)
    absorb = make_user(db)
    absorb.status = UserStatus.banned
    db.commit()

    survivor = user_auth.merge_accounts(db, keep=keep, absorb=absorb)
    db.commit()

    assert survivor.status == UserStatus.banned


def test_merging_an_account_into_itself_does_nothing(db):
    user = make_user(db)
    db.commit()
    assert user_auth.merge_accounts(db, keep=user, absorb=user).id == user.id
    assert db.get(User, user.id) is not None


def test_outstanding_link_codes_move_with_the_absorbed_account(db):
    """A dangling code would point at a deleted user and 500 on redemption."""
    keep = make_user(db)
    absorb = make_user(db)
    code = user_auth.start_link(db, absorb).code
    db.commit()

    user_auth.merge_accounts(db, keep=keep, absorb=absorb)
    db.commit()

    assert db.query(LinkCode).filter_by(code=code).one().user_id == keep.id


def test_a_merge_does_not_destroy_the_absorbed_identities(db):
    """Regression: both collections cascade delete-orphan, so a child whose
    foreign key was reassigned by hand still sits in the old parent's loaded
    collection and gets deleted along with it. The user would tap "link
    account" and find themselves signed out."""
    keep = make_user(db)
    db.add(AuthIdentity(user_id=keep.id, provider=AuthProvider.telegram, provider_uid="555"))
    absorbed = user_auth.register_device(db)
    db.commit()

    user_auth.merge_accounts(db, keep=keep, absorb=db.get(User, absorbed.user_id))
    db.commit()

    assert user_auth.authenticate(db, absorbed.token).id == keep.id
    providers = {i.provider for i in db.query(AuthIdentity).filter_by(user_id=keep.id).all()}
    assert providers == {AuthProvider.telegram, AuthProvider.device}
