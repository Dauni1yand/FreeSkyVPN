"""Test doubles for the head API and the Telegram bot."""

from __future__ import annotations

from dataclasses import dataclass, field

from botapp.api_client import PendingPush


@dataclass
class FakeHeadApi:
    pushes: list[PendingPush] = field(default_factory=list)
    acked: list[tuple[str, str | None]] = field(default_factory=list)
    fail_on_pending: Exception | None = None

    async def pending_pushes(self, limit: int = 50) -> list[PendingPush]:
        if self.fail_on_pending is not None:
            raise self.fail_on_pending
        return self.pushes[:limit]

    async def ack_push(self, push_id: str, error: str | None = None) -> None:
        self.acked.append((push_id, error))


@dataclass
class SentMessage:
    chat_id: int
    text: str


@dataclass
class FakeBot:
    sent: list[SentMessage] = field(default_factory=list)
    raise_for_chat: dict[int, Exception] = field(default_factory=dict)

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self.raise_for_chat:
            raise self.raise_for_chat[chat_id]
        self.sent.append(SentMessage(chat_id=chat_id, text=text))


def make_push(push_id: str = "p1", telegram_id: str | None = "123", vless_url: str | None = "vless://x") -> PendingPush:
    return PendingPush(
        push_id=push_id,
        user_id="u1",
        telegram_id=telegram_id,
        reason="inbound_blocked",
        vless_url=vless_url,
    )
