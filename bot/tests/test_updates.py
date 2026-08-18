"""The bot's half of the Xray update flow.

Two things are load-bearing and both are tested here: that nobody but the
configured admin can authorise a fleet restart, and that a question which
was never delivered gets asked again rather than quietly lost.
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramRetryAfter

from botapp import keyboards, texts
from botapp.config import get_settings
from botapp.handlers import _decide_update, _is_admin
from botapp.updates import announce_pending, report_results
from tests.fakes import FakeBot, FakeHeadApi, make_update_group, make_update_result

ADMIN_CHAT = "4242"


@pytest.fixture(autouse=True)
def admin_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", ADMIN_CHAT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- announcing ----------------------------------------------------------


async def test_one_message_per_version_with_the_decision_buttons():
    api = FakeHeadApi(update_groups=[make_update_group(hosts=("a", "b", "c"))])
    bot = FakeBot()

    sent = await announce_pending(bot, api, ADMIN_CHAT)

    assert sent == 1
    message = bot.sent[0]
    assert message.chat_id == 4242
    assert "26.3.27" in message.text
    buttons = [b.callback_data for row in message.reply_markup.inline_keyboard for b in row]
    assert f"{keyboards.CB_UPD_APPROVE_PREFIX}26.3.27" in buttons
    assert f"{keyboards.CB_UPD_DECLINE_PREFIX}26.3.27" in buttons


async def test_the_message_names_the_nodes_and_warns_about_the_restart():
    api = FakeHeadApi(update_groups=[make_update_group(hosts=("203.0.113.10",))])
    bot = FakeBot()

    await announce_pending(bot, api, ADMIN_CHAT)

    text = bot.sent[0].text
    assert "203.0.113.10" in text
    assert "оборвутся" in text, "the operator must know connections drop"


async def test_an_announcement_is_acknowledged_only_after_telegram_accepted_it():
    api = FakeHeadApi(update_groups=[make_update_group()])
    bot = FakeBot(raise_for_chat={4242: RuntimeError("telegram down")})

    sent = await announce_pending(bot, api, ADMIN_CHAT)

    assert sent == 0
    assert api.acked_notifications == [], "the next tick must ask again"


async def test_flood_control_pauses_rather_than_dropping_the_question():
    api = FakeHeadApi(update_groups=[make_update_group()])
    bot = FakeBot(
        raise_for_chat={4242: TelegramRetryAfter(method=None, message="flood", retry_after=30)}
    )

    await announce_pending(bot, api, ADMIN_CHAT)

    assert api.acked_notifications == []


async def test_every_row_in_the_group_is_acknowledged_together():
    api = FakeHeadApi(update_groups=[make_update_group(hosts=("a", "b", "c"))])
    bot = FakeBot()

    await announce_pending(bot, api, ADMIN_CHAT)

    assert api.acked_notifications == [["u0", "u1", "u2"]]


# --- reporting outcomes --------------------------------------------------


async def test_a_successful_update_is_reported_with_both_versions():
    api = FakeHeadApi(update_outcomes=[make_update_result()])
    bot = FakeBot()

    reported = await report_results(bot, api, ADMIN_CHAT)

    assert reported == 1
    assert "26.3.20" in bot.sent[0].text
    assert "26.3.27" in bot.sent[0].text


async def test_a_failed_update_says_the_node_still_works():
    api = FakeHeadApi(
        update_outcomes=[
            make_update_result(status="failed", version_after=None, error="ssh timed out")
        ]
    )
    bot = FakeBot()

    await report_results(bot, api, ADMIN_CHAT)

    text = bot.sent[0].text
    assert "ssh timed out" in text
    assert "прежней версии" in text


async def test_an_image_lagging_the_release_reads_as_success_with_a_note():
    """It ran and the node came back — just still behind upstream. Reporting
    that as a failure would send the operator hunting a healthy node."""
    api = FakeHeadApi(
        update_outcomes=[
            make_update_result(version_after="26.3.25", error="образ ещё не содержит 26.3.27")
        ]
    )
    bot = FakeBot()

    await report_results(bot, api, ADMIN_CHAT)

    text = bot.sent[0].text
    assert text.startswith("✅")
    assert "образ ещё не содержит" in text


async def test_an_undeliverable_result_is_acknowledged_rather_than_retried_forever():
    api = FakeHeadApi(update_outcomes=[make_update_result("u9")])
    bot = FakeBot(raise_for_chat={4242: RuntimeError("telegram down")})

    reported = await report_results(bot, api, ADMIN_CHAT)

    assert reported == 0
    assert api.acked_results == [["u9"]], "it is still visible in the admin panel"


# --- authorisation -------------------------------------------------------


def test_only_the_configured_chat_is_admin():
    assert _is_admin(4242)
    assert not _is_admin(9999)


def test_nobody_is_admin_when_no_chat_is_configured(monkeypatch):
    """An unset admin chat must fail closed. Treating "unset" as "everyone"
    would let any user restart the fleet."""
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    get_settings.cache_clear()

    assert not _is_admin(4242)
    assert not _is_admin(0)


# --- the decision callback ----------------------------------------------


class _FakeMessage:
    def __init__(self):
        self.text: str | None = None
        self.markup_cleared = False

    async def edit_text(self, text, **_kw):
        self.text = text

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_cleared = reply_markup is None


class _FakeCallback:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = type("User", (), {"id": user_id})()
        self.message = _FakeMessage()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


async def test_a_non_admin_tapping_the_button_changes_nothing():
    """callback_data is visible to anyone who can see the message, and a
    forwarded message carries its buttons — so the gate cannot be the
    keyboard."""
    api = FakeHeadApi()
    callback = _FakeCallback(f"{keyboards.CB_UPD_APPROVE_PREFIX}26.3.27", user_id=9999)

    await _decide_update(callback, api, approve=True)

    assert api.decisions == []
    assert callback.answers == [(texts.NOT_ADMIN, True)]


async def test_the_admin_approving_records_the_version_and_who_asked():
    api = FakeHeadApi()
    callback = _FakeCallback(f"{keyboards.CB_UPD_APPROVE_PREFIX}26.3.27", user_id=4242)

    await _decide_update(callback, api, approve=True)

    assert api.decisions == [("26.3.27", True, "telegram:4242")]
    assert "26.3.27" in callback.message.text


async def test_declining_says_it_will_not_ask_again():
    api = FakeHeadApi()
    callback = _FakeCallback(f"{keyboards.CB_UPD_DECLINE_PREFIX}26.3.27", user_id=4242)

    await _decide_update(callback, api, approve=False)

    assert api.decisions == [("26.3.27", False, "telegram:4242")]
    assert "не напомню" in callback.message.text


async def test_a_second_tap_says_it_was_already_decided():
    """The same update can be approved from the admin panel a second earlier.
    The button must not imply it started anything."""
    api = FakeHeadApi(decide_changes=0)
    callback = _FakeCallback(f"{keyboards.CB_UPD_APPROVE_PREFIX}26.3.27", user_id=4242)

    await _decide_update(callback, api, approve=True)

    assert callback.answers == [(texts.UPDATE_ALREADY_DECIDED, True)]
    assert callback.message.markup_cleared, "the stale buttons should go away"


async def test_a_version_with_dots_survives_the_callback_round_trip():
    api = FakeHeadApi()
    callback = _FakeCallback(f"{keyboards.CB_UPD_APPROVE_PREFIX}1.8.24", user_id=4242)

    await _decide_update(callback, api, approve=True)

    assert api.decisions[0][0] == "1.8.24"


def test_callback_data_fits_telegram_budget():
    """64 bytes is a hard Telegram limit, and it is the whole reason the
    button carries a version rather than a list of row UUIDs — three of
    those would already be over."""
    markup = keyboards.update_decision("26.3.27", 12)

    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
