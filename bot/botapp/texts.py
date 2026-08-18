"""All user-facing copy in one place.

Kept separate from handler logic so wording can be revised without touching
behaviour, and so the Android app can mirror the same phrasing later.

The config message deliberately does not explain Reality, SNI or ports. The
product is one button; a user who needs a working connection should not have
to learn the transport to get one.
"""

from __future__ import annotations

from datetime import datetime

WELCOME = (
    "👋 <b>FreeSkyVPN</b>\n\n"
    "Нажмите «Подключиться» — вы получите ссылку для вашего VPN-клиента.\n"
    "Если соединение перестанет работать, нажмите «Не работает» и получите новую."
)

CONNECTING = "⏳ Подбираю сервер…"

NO_CAPACITY = (
    "😔 Сейчас нет доступных серверов. Мы уже разбираемся — попробуйте через несколько минут."
)

GENERIC_ERROR = "⚠️ Что-то пошло не так. Попробуйте ещё раз через минуту."

REPORT_TOO_SOON = "⏱ Слишком часто. Попробуйте ещё раз через {seconds} сек."

NO_ACTIVE_CONFIG = "У вас пока нет активной конфигурации — нажмите «Подключиться»."

TRIAL_ALREADY_USED = "Пробный период уже был использован на этом аккаунте."

PAYMENTS_DISABLED = (
    "💳 Оплата пока не подключена. Пробный период и бесплатный доступ работают как обычно."
)


def config_message(vless_url: str) -> str:
    return (
        "✅ <b>Готово</b>\n\n"
        "Скопируйте ссылку и добавьте её в ваш VPN-клиент:\n\n"
        f"<code>{vless_url}</code>"
    )


def new_config_pushed(vless_url: str) -> str:
    return (
        "🔄 <b>Мы обновили вашу конфигурацию</b>\n\n"
        "Прежний сервер перестал работать, мы заранее выдали новый — "
        "просто замените ссылку в клиенте:\n\n"
        f"<code>{vless_url}</code>"
    )


def failure_handled(vless_url: str, inbound_dead: bool) -> str:
    header = (
        "🛠 <b>Нашли проблему и починили</b>\n\nСервер был заблокирован, выдали новый:"
        if inbound_dead
        else "🔄 <b>Выдали другой сервер</b>\n\nПопробуйте эту ссылку:"
    )
    return f"{header}\n\n<code>{vless_url}</code>"


def _format_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso


def subscription_status(active: bool, sub_type: str | None, expires_at: str | None) -> str:
    if not active:
        return (
            "📋 <b>Ваш тариф: бесплатный</b>\n\n"
            "Скорость ограничена. Оформите подписку — уберём ограничение и рекламу, "
            "и дадим приоритет на серверах."
        )

    label = "пробный период" if sub_type == "trial" else "платная подписка"
    return (
        f"📋 <b>Ваш тариф: {label}</b>\n\n"
        f"Действует до: <b>{_format_date(expires_at)}</b>\n\n"
        "Максимальная скорость, без рекламы, приоритет на серверах."
    )


def trial_started(expires_at: str | None) -> str:
    return (
        "🎁 <b>Пробный период активирован</b>\n\n"
        f"7 дней полного доступа до <b>{_format_date(expires_at)}</b>: "
        "максимальная скорость, без рекламы."
    )


def payment_succeeded(expires_at: str | None) -> str:
    return (
        "🎉 <b>Оплата прошла</b>\n\n"
        f"Подписка действует до <b>{_format_date(expires_at)}</b>. Спасибо!"
    )


def invoice_description(plan_name: str, duration_days: int) -> str:
    return f"{plan_name} — максимальная скорость, без рекламы, приоритет на серверах ({duration_days} дн.)"
