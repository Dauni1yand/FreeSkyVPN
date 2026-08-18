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


# --- Xray updates (admin only) ------------------------------------------
#
# Written for the one person who runs the service, not for users, so unlike
# everything above it does name versions and hosts: the decision being asked
# for is an operational one and needs the operational detail.

NOT_ADMIN = "Эта кнопка только для администратора."

UPDATE_ALREADY_DECIDED = "Это обновление уже решено — возможно, из админки."


def update_available(target_version: str, nodes: list) -> str:
    lines = [
        "⬆️ <b>Вышла новая версия Xray</b>",
        "",
        f"Версия: <code>{target_version}</code>",
        f"Затронуто нод: <b>{len(nodes)}</b>",
        "",
    ]
    for node in nodes[:10]:
        current = node.version_before or "неизвестно"
        lines.append(f"• <code>{node.host}</code> ({node.country}) — сейчас {current}")
    if len(nodes) > 10:
        lines.append(f"… и ещё {len(nodes) - 10}")

    lines += [
        "",
        (
            "Обновление пересоздаёт контейнер на ноде: активные подключения на ней "
            "оборвутся, клиенты переподключатся сами. Ноды обновляются по одной."
        ),
        "",
        "Ничего не произойдёт, пока вы не нажмёте кнопку.",
    ]
    return "\n".join(lines)


def update_queued(target_version: str, count: int) -> str:
    return (
        f"✅ Обновление до <code>{target_version}</code> подтверждено для {count} нод(ы).\n"
        "Ставится по одной, о результате сообщу здесь же."
    )


def update_declined(target_version: str) -> str:
    return (
        f"Ок, <code>{target_version}</code> пока не ставим.\n"
        "Про эту версию больше не напомню — запустить можно из админки, раздел «Обновления»."
    )


def update_result(result) -> str:
    if result.status == "applied":
        head = (
            f"✅ <code>{result.host}</code> ({result.country}): Xray "
            f"{result.version_before or '?'} → <b>{result.version_after}</b>"
        )
        # The image lagging the release is the common case and is not a
        # failure, so it is a footnote rather than a warning.
        return f"{head}\n\n{result.error}" if result.error else head

    return (
        f"⚠️ <code>{result.host}</code> ({result.country}): обновить до "
        f"{result.target_version} не удалось.\n\n"
        f"{result.error or 'причина неизвестна'}\n\n"
        "Нода продолжает работать на прежней версии."
    )


# --- linking the Android app --------------------------------------------

LINK_USAGE = (
    "Чтобы связать приложение с этим аккаунтом, пришлите код из приложения:\n\n"
    "<code>/link 123456</code>\n\n"
    "Код показан в приложении, раздел «Аккаунт». Он живёт 10 минут."
)

LINK_OK = (
    "✅ <b>Приложение привязано</b>\n\n"
    "Подписка и пробный период теперь общие для бота и приложения."
)


def link_failed(reason: str) -> str:
    return f"❌ Не получилось: {reason}\n\nЗапросите новый код в приложении и пришлите его сюда."
