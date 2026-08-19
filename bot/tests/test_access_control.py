"""The bot is an operator's console, and the allowlist is what makes it one.

Two of the things it can do are dangerous in public hands: granting access
bypasses the advertising that pays for the servers, and approving an update
restarts nodes. So the interesting tests here are the refusals, and
especially the one about an empty configuration — a bot that answered
everyone because nobody filled in a variable is the failure that shows up
in the bill rather than in the logs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, User

from botapp.access_control import AdminOnlyMiddleware, allowed_chat_ids, is_allowed
from botapp.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch, admin: str = "", allowed: str = ""):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", admin)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", allowed)
    get_settings.cache_clear()


# --- who is allowed ------------------------------------------------------


def test_the_admin_chat_is_always_allowed(monkeypatch):
    _configure(monkeypatch, admin="4242")
    assert is_allowed(4242)


def test_extra_chats_can_be_listed(monkeypatch):
    _configure(monkeypatch, admin="4242", allowed="111, 222")
    assert is_allowed(111)
    assert is_allowed(222)
    assert is_allowed(4242)


def test_everyone_else_is_refused(monkeypatch):
    _configure(monkeypatch, admin="4242", allowed="111")
    assert not is_allowed(999)


def test_an_empty_configuration_allows_nobody(monkeypatch):
    """The safe direction. A bot that answered everyone because a variable
    was blank would be a hole nobody notices until the servers cost money."""
    _configure(monkeypatch)
    assert allowed_chat_ids() == set()
    assert not is_allowed(4242)


def test_a_missing_sender_is_refused(monkeypatch):
    _configure(monkeypatch, admin="4242")
    assert not is_allowed(None)


def test_whitespace_in_the_list_is_tolerated(monkeypatch):
    """It is typed into a .env by hand."""
    _configure(monkeypatch, admin=" 4242 ", allowed=" 111 , 222 ,")
    assert allowed_chat_ids() == {"4242", "111", "222"}


# --- the middleware ------------------------------------------------------


def _message(user_id: int | None, answers: list[str]) -> Message:
    """A real aiogram Message.

    Built properly rather than duck-typed: the middleware branches on
    `isinstance`, so a stand-in object would take a different path through
    the code than production does and prove nothing about it.
    """
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id or 0, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="T") if user_id else None,
        text="/start",
    )

    async def answer(text, **_kw):
        answers.append(text)

    # model_copy so pydantic's validation does not reject a bound method.
    object.__setattr__(message, "answer", answer)
    return message


@pytest.fixture
def calls():
    seen = []

    async def handler(event, data):
        seen.append(event)
        return "handled"

    return seen, handler


async def test_an_allowed_chat_reaches_the_handler(monkeypatch, calls):
    _configure(monkeypatch, admin="4242")
    seen, handler = calls
    message = _message(4242, [])

    result = await AdminOnlyMiddleware()(handler, message, {})

    assert result == "handled"
    assert seen == [message]


async def test_a_stranger_never_reaches_the_handler(monkeypatch, calls):
    _configure(monkeypatch, admin="4242")
    seen, handler = calls
    message = _message(999, [])

    result = await AdminOnlyMiddleware()(handler, message, {})

    assert result is None
    assert seen == [], "the handler must not run at all"


async def test_a_stranger_is_told_where_the_product_is(monkeypatch, calls):
    """Silence looks like a broken bot rather than a closed one."""
    _configure(monkeypatch, admin="4242")
    _seen, handler = calls
    answers: list[str] = []

    await AdminOnlyMiddleware()(handler, _message(999, answers), {})

    assert answers, "a refusal should still say something"
    assert "приложени" in answers[0].lower()


async def test_an_unconfigured_bot_refuses_everyone(monkeypatch, calls):
    _configure(monkeypatch)
    seen, handler = calls

    await AdminOnlyMiddleware()(handler, _message(4242, []), {})

    assert seen == []
