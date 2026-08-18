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
class Subscription:
    active: bool
    type: str | None
    expires_at: str | None
    plan_code: str | None
    trial_available: bool


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    duration_days: int
    price: float
    currency: str


@dataclass(frozen=True)
class PendingPush:
    push_id: str
    user_id: str
    telegram_id: str | None
    reason: str
    vless_url: str | None


class HeadApi:
    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=settings.head_api_url,
            headers={"X-Service-Token": settings.head_service_token},
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
    async def subscription(self, user_id: str) -> Subscription:
        data = await self._post("/api/v1/subscription", {"user_id": user_id})
        return Subscription(
            active=data["active"],
            type=data["type"],
            expires_at=data["expires_at"],
            plan_code=data["plan_code"],
            trial_available=data["trial_available"],
        )

    async def start_trial(self, user_id: str) -> Subscription:
        data = await self._post("/api/v1/subscription/trial", {"user_id": user_id})
        return Subscription(
            active=data["active"],
            type=data["type"],
            expires_at=data["expires_at"],
            plan_code=data["plan_code"],
            trial_available=data["trial_available"],
        )

    async def plans(self) -> list[Plan]:
        rows = await self._get("/api/v1/plans")
        return [
            Plan(
                code=r["code"],
                name=r["name"],
                duration_days=r["duration_days"],
                price=r["price"],
                currency=r["currency"],
            )
            for r in rows
        ]

    async def confirm_payment(
        self, user_id: str, plan_code: str, provider_payment_id: str, amount: float, currency: str
    ) -> Subscription:
        data = await self._post(
            "/api/v1/payments/confirm",
            {
                "user_id": user_id,
                "plan_code": plan_code,
                "provider": "telegram",
                "provider_payment_id": provider_payment_id,
                "amount": amount,
                "currency": currency,
            },
        )
        return Subscription(
            active=data["active"],
            type=data["type"],
            expires_at=data["expires_at"],
            plan_code=data["plan_code"],
            trial_available=data["trial_available"],
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
