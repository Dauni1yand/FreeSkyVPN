"""Keyboards.

The main menu is deliberately four buttons and no server list: choosing a
country is the head's job, not the user's (blueprint §07).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from botapp.api_client import Plan

CB_CONNECT = "connect"
CB_REPORT = "report_failure"
CB_SUBSCRIPTION = "subscription"
CB_TRIAL = "trial"
CB_BUY_PREFIX = "buy:"
CB_MENU = "menu"
# Xray updates. The version travels in the callback data rather than a list
# of row ids: Telegram caps callback_data at 64 bytes, and a fleet of five
# nodes would already blow that budget with UUIDs.
CB_UPD_APPROVE_PREFIX = "upd_ok:"
CB_UPD_DECLINE_PREFIX = "upd_no:"


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 Подключиться", callback_data=CB_CONNECT)],
            [InlineKeyboardButton(text="🛠 Не работает", callback_data=CB_REPORT)],
            [InlineKeyboardButton(text="⭐ Подписка", callback_data=CB_SUBSCRIPTION)],
        ]
    )


def subscription_menu(plans: list[Plan], trial_available: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if trial_available:
        builder.row(InlineKeyboardButton(text="🎁 7 дней бесплатно", callback_data=CB_TRIAL))

    for plan in plans:
        price = int(plan.price) if plan.price == int(plan.price) else plan.price
        builder.row(
            InlineKeyboardButton(
                text=f"{plan.name} — {price} {plan.currency}",
                callback_data=f"{CB_BUY_PREFIX}{plan.code}",
            )
        )

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU))
    return builder.as_markup()


def update_decision(target_version: str, node_count: int) -> InlineKeyboardMarkup:
    """Approve or decline one Xray version, for every node it applies to."""
    label = "Обновить" if node_count == 1 else f"Обновить все ({node_count})"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⬆️ {label}", callback_data=f"{CB_UPD_APPROVE_PREFIX}{target_version}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✋ Не сейчас", callback_data=f"{CB_UPD_DECLINE_PREFIX}{target_version}"
                )
            ],
        ]
    )
