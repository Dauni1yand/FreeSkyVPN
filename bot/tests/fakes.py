"""Test doubles for the head API and the Telegram bot."""

from __future__ import annotations

from dataclasses import dataclass, field

from botapp.api_client import PendingPush, UpdateGroup, UpdateNode, UpdateResult


@dataclass
class FakeHeadApi:
    pushes: list[PendingPush] = field(default_factory=list)
    acked: list[tuple[str, str | None]] = field(default_factory=list)
    fail_on_pending: Exception | None = None

    # --- Xray updates ---
    update_groups: list[UpdateGroup] = field(default_factory=list)
    update_outcomes: list[UpdateResult] = field(default_factory=list)
    acked_notifications: list[list[str]] = field(default_factory=list)
    acked_results: list[list[str]] = field(default_factory=list)
    decisions: list[tuple[str, bool, str]] = field(default_factory=list)
    # How many rows the head reports as changed; 0 models "already decided".
    decide_changes: int = 1

    async def pending_pushes(self, limit: int = 50) -> list[PendingPush]:
        if self.fail_on_pending is not None:
            raise self.fail_on_pending
        return self.pushes[:limit]

    async def ack_push(self, push_id: str, error: str | None = None) -> None:
        self.acked.append((push_id, error))

    async def pending_update_groups(self) -> list[UpdateGroup]:
        return self.update_groups

    async def ack_update_notifications(self, update_ids: list[str]) -> None:
        self.acked_notifications.append(update_ids)

    async def update_results(self) -> list[UpdateResult]:
        return self.update_outcomes

    async def ack_update_results(self, update_ids: list[str]) -> None:
        self.acked_results.append(update_ids)

    async def decide_update_version(self, target_version: str, *, approve: bool, by: str) -> int:
        self.decisions.append((target_version, approve, by))
        return self.decide_changes


@dataclass
class SentMessage:
    chat_id: int
    text: str
    reply_markup: object = None


@dataclass
class FakeBot:
    sent: list[SentMessage] = field(default_factory=list)
    raise_for_chat: dict[int, Exception] = field(default_factory=dict)

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        if chat_id in self.raise_for_chat:
            raise self.raise_for_chat[chat_id]
        self.sent.append(SentMessage(chat_id=chat_id, text=text, reply_markup=reply_markup))


def make_push(push_id: str = "p1", telegram_id: str | None = "123", vless_url: str | None = "vless://x") -> PendingPush:
    return PendingPush(
        push_id=push_id,
        user_id="u1",
        telegram_id=telegram_id,
        reason="inbound_blocked",
        vless_url=vless_url,
    )


def make_update_group(
    target_version: str = "26.3.27",
    *,
    hosts: tuple[str, ...] = ("203.0.113.10",),
    version_before: str | None = "26.3.20",
) -> UpdateGroup:
    nodes = [
        UpdateNode(
            update_id=f"u{i}", host=host, country="nl", version_before=version_before
        )
        for i, host in enumerate(hosts)
    ]
    return UpdateGroup(
        target_version=target_version,
        update_ids=[n.update_id for n in nodes],
        nodes=nodes,
    )


def make_update_result(
    update_id: str = "u0",
    *,
    status: str = "applied",
    version_after: str | None = "26.3.27",
    error: str | None = None,
) -> UpdateResult:
    return UpdateResult(
        update_id=update_id,
        host="203.0.113.10",
        country="nl",
        target_version="26.3.27",
        version_before="26.3.20",
        version_after=version_after,
        status=status,
        error=error,
    )
