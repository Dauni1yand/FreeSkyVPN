"""Keyboards.

Three buttons and no server list: choosing a country is the head's job, not
the user's. There is no subscription button because there is no
subscription — access is bought with attention, in the app, which is the
only place a rewarded video can be shown.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_CONNECT = "connect"
CB_REPORT = "report_failure"
CB_ACCESS = "access"
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
            [InlineKeyboardButton(text="🔑 Доступ", callback_data=CB_ACCESS)],
        ]
    )


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
