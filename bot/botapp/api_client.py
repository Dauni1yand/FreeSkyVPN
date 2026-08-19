"""Typed client for the head API.

The bot holds no state and no database of its own — every decision (which
node, which inbound, whether a config is dead, what a user is entitled to)
belongs to the head. That keeps this a thin client, and means the Android
app in phase 5 can speak the same API without reimplementing any logic.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from botapp.config import get_settings


class HeadApiError(RuntimeError):
    """The head rejected or failed a call."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"head API returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Config:
    vless_url: str
    node_country: str


@dataclass(frozen=True)
class FailureResult:
    config: Config
    inbound_declared_dead: bool
    users_migrated: int


@dataclass(frozen=True)
class Account:
    user_id: str
    access_active: bool
    access_seconds_remaining: int
    access_is_grace: bool


@dataclass(frozen=True)
class PendingPush:
    push_id: str
    user_id: str
    telegram_id: str | None
    reason: str
    vless_url: str | None


@dataclass(frozen=True)
class UpdateNode:
    update_id: str
    host: str
    country: str
    version_before: str | None


@dataclass(frozen=True)
class UpdateGroup:
    """One Xray version, and every node that could move to it.

    Grouped by the head rather than by the bot: approving a fleet-wide
    release should be one tap, and which rows belong together is the head's
    knowledge, not the bot's.
    """

    target_version: str
    update_ids: list[str]
    nodes: list[UpdateNode]


@dataclass(frozen=True)
class UpdateResult:
    update_id: str
    host: str
    country: str
    target_version: str
    version_before: str | None
    version_after: str | None
    status: str
    error: str | None


class HeadApi:
    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=settings.head_api_url,
            headers={"X-Admin-Token": settings.head_admin_token},
            timeout=20.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        resp = await self._client.post(path, json=payload)
        return self._unwrap(resp)

    async def _get(self, path: str, params: dict | None = None) -> list | dict:
        resp = await self._client.get(path, params=params)
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> list | dict:
        if resp.is_success:
            return resp.json()
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise HeadApiError(resp.status_code, str(detail))

    # --- identity -------------------------------------------------------
    async def login_telegram(self, telegram_id: int) -> str:
        data = await self._post("/api/v1/auth/telegram", {"telegram_id": telegram_id})
        return data["user_id"]

    # --- config ---------------------------------------------------------
    async def connect(self, user_id: str) -> Config:
        data = await self._post("/api/v1/connect", {"user_id": user_id})
        return Config(vless_url=data["vless_url"], node_country=data["node_country"])

    async def report_failure(self, user_id: str) -> FailureResult:
        data = await self._post("/api/v1/report-failure", {"user_id": user_id})
        return FailureResult(
            config=Config(vless_url=data["vless_url"], node_country=data["node_country"]),
            inbound_declared_dead=data["inbound_declared_dead"],
            users_migrated=data["users_migrated"],
        )

    # --- billing --------------------------------------------------------
    async def grant_test_access(self, user_id: str) -> Account:
        """Put a whitelisted tester online without an ad.

        Deliberately a distinct call rather than a shared one: it bypasses
        the advertising the service runs on, so it should be obvious in the
        code and traceable in the head's records.
        """
        data = await self._post("/api/v1/admin/grant-access", {"user_id": user_id})
        return Account(
            user_id=data["user_id"],
            access_active=data["access_active"],
            access_seconds_remaining=data["access_seconds_remaining"],
            access_is_grace=data["access_is_grace"],
        )

    # --- outbox ---------------------------------------------------------
    async def pending_pushes(self, limit: int = 50) -> list[PendingPush]:
        rows = await self._get("/api/v1/pushes/pending", {"limit": limit})
        return [
            PendingPush(
                push_id=r["push_id"],
                user_id=r["user_id"],
                telegram_id=r["telegram_id"],
                reason=r["reason"],
                vless_url=r["vless_url"],
            )
            for r in rows
        ]

    async def ack_push(self, push_id: str, error: str | None = None) -> None:
        await self._post("/api/v1/pushes/ack", {"push_id": push_id, "error": error})

    # --- Xray updates ---------------------------------------------------
    async def pending_update_groups(self) -> list[UpdateGroup]:
        rows = await self._get("/api/v1/xray-updates/notifications")
        return [
            UpdateGroup(
                target_version=r["target_version"],
                update_ids=[str(i) for i in r["update_ids"]],
                nodes=[
                    UpdateNode(
                        update_id=str(n["update_id"]),
                        host=n["host"],
                        country=n["country"],
                        version_before=n["version_before"],
                    )
                    for n in r["nodes"]
                ],
            )
            for r in rows
        ]

    async def ack_update_notifications(self, update_ids: list[str]) -> None:
        await self._post("/api/v1/xray-updates/notifications/ack", {"update_ids": update_ids})

    async def update_results(self) -> list[UpdateResult]:
        rows = await self._get("/api/v1/xray-updates/results")
        return [
            UpdateResult(
                update_id=str(r["update_id"]),
                host=r["host"],
                country=r["country"],
                target_version=r["target_version"],
                version_before=r["version_before"],
                version_after=r["version_after"],
                status=r["status"],
                error=r["error"],
            )
            for r in rows
        ]

    async def ack_update_results(self, update_ids: list[str]) -> None:
        await self._post("/api/v1/xray-updates/results/ack", {"update_ids": update_ids})

    async def decide_update_version(self, target_version: str, *, approve: bool, by: str) -> int:
        """Answer for every node still waiting on this Xray version.

        By version rather than by row id because that is all a Telegram
        callback can carry, and because the operator was asked about a
        release rather than about a list of rows.
        """
        data = await self._post(
            "/api/v1/xray-updates/decide",
            {"target_version": target_version, "approve": approve, "by": by},
        )
        return int(data["changed"])

    # --- linking the Android app to this Telegram account ---------------
    async def redeem_link_code(self, code: str, telegram_id: int) -> str:
        """Attach this Telegram account to the app account that showed `code`.

        The bot is the only caller that legitimately has a Telegram id: it
        saw the message arrive from it. The app cannot prove one about
        itself, which is why redemption lives here and not there.
        """
        data = await self._post(
            "/api/v1/auth/link/redeem",
            {"code": code, "telegram_id": str(telegram_id)},
        )
        return data["user_id"]
