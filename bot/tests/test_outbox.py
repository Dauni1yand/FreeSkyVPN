import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from botapp.outbox import deliver_pending
from tests.fakes import FakeBot, FakeHeadApi, make_push


async def test_delivers_and_acknowledges_each_push():
    api = FakeHeadApi(pushes=[make_push("p1", "111"), make_push("p2", "222")])
    bot = FakeBot()

    delivered = await deliver_pending(bot, api)

    assert delivered == 2
    assert [m.chat_id for m in bot.sent] == [111, 222]
    assert api.acked == [("p1", None), ("p2", None)]


async def test_message_carries_the_new_config():
    api = FakeHeadApi(pushes=[make_push(vless_url="vless://the-new-one")])
    bot = FakeBot()

    await deliver_pending(bot, api)

    assert "vless://the-new-one" in bot.sent[0].text


async def test_push_is_not_acknowledged_when_sending_fails_midway():
    """A crash before Telegram accepted the message must redeliver, not drop."""
    api = FakeHeadApi(pushes=[make_push("p1", "111")])
    bot = FakeBot(raise_for_chat={111: TelegramRetryAfter(method=None, message="flood", retry_after=30)})

    delivered = await deliver_pending(bot, api)

    assert delivered == 0
    assert api.acked == [], "the row must stay pending so the next tick retries it"


async def test_flood_control_pauses_the_batch_rather_than_burning_it():
    api = FakeHeadApi(pushes=[make_push("p1", "111"), make_push("p2", "222")])
    bot = FakeBot(raise_for_chat={111: TelegramRetryAfter(method=None, message="flood", retry_after=30)})

    await deliver_pending(bot, api)

    assert bot.sent == [], "the batch stops at the first rate limit"
    assert api.acked == []


async def test_blocked_user_is_dropped_rather_than_retried_forever():
    api = FakeHeadApi(pushes=[make_push("p1", "111"), make_push("p2", "222")])
    bot = FakeBot(raise_for_chat={111: TelegramForbiddenError(method=None, message="bot was blocked")})

    delivered = await deliver_pending(bot, api)

    assert delivered == 1
    assert [m.chat_id for m in bot.sent] == [222]
    p1_ack = next(ack for ack in api.acked if ack[0] == "p1")
    assert p1_ack[1] is not None, "the failure reason is recorded"


async def test_push_without_a_telegram_identity_is_dropped():
    """An Android-only account cannot be reached here; the row must not wedge the queue."""
    api = FakeHeadApi(pushes=[make_push("p1", telegram_id=None)])
    bot = FakeBot()

    delivered = await deliver_pending(bot, api)

    assert delivered == 0
    assert bot.sent == []
    assert api.acked[0][0] == "p1"
    assert api.acked[0][1] is not None


async def test_push_without_an_active_config_is_dropped():
    api = FakeHeadApi(pushes=[make_push("p1", vless_url=None)])
    bot = FakeBot()

    await deliver_pending(bot, api)

    assert bot.sent == []
    assert api.acked[0][0] == "p1"


async def test_head_being_unreachable_propagates_for_the_worker_to_retry():
    api = FakeHeadApi(fail_on_pending=RuntimeError("head down"))
    bot = FakeBot()

    with pytest.raises(RuntimeError):
        await deliver_pending(bot, api)
