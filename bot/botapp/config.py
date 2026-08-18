from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""

    head_api_url: str = "http://localhost:8000"
    # must match HEAD_SECRET_KEY on the head (see head/app/api/auth.py)
    head_service_token: str = ""

    # Telegram Payments provider token, issued via @BotFather -> Payments.
    # Empty disables the buy flow, leaving the trial and free tier usable —
    # handy for running the bot before payments are set up.
    payment_provider_token: str = ""

    # how often the outbox worker drains pending config pushes
    outbox_poll_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
