"""Shared Telegram keyboard layouts shown under generated deck images."""

from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup


DECK_BUTTON_LAYOUT_FULL = "full"
DECK_BUTTON_LAYOUT_COMPACT = "compact"
DECK_BUTTON_LAYOUT_COPY_ONLY = "copy_only"

DECK_BUTTON_LAYOUT_OPTIONS = {
    DECK_BUTTON_LAYOUT_FULL: "Как сейчас · код / скачать / избранное",
    DECK_BUTTON_LAYOUT_COMPACT: "Код + избранное · один ряд",
    DECK_BUTTON_LAYOUT_COPY_ONLY: "Только скопировать код",
}


def normalize_deck_button_layout(value: object) -> str:
    normalized = str(value or DECK_BUTTON_LAYOUT_FULL).strip().lower()
    if normalized in DECK_BUTTON_LAYOUT_OPTIONS:
        return normalized
    return DECK_BUTTON_LAYOUT_FULL


def build_deck_action_keyboard(
    deck_code: str,
    download_key: str,
    generated_deck_id: int | None,
    layout: object = DECK_BUTTON_LAYOUT_FULL,
) -> InlineKeyboardMarkup:
    """Build a stable keyboard without touching storage or the render path."""
    selected = normalize_deck_button_layout(layout)
    copy_button = InlineKeyboardButton(
        text="Скопировать код",
        style="primary",
        icon_custom_emoji_id="5877301185639091664",
        copy_text=CopyTextButton(text=str(deck_code)),
    )
    favorite_button = (
        InlineKeyboardButton(
            text=(
                "Сохранить"
                if selected == DECK_BUTTON_LAYOUT_FULL
                else "В избранное"
            ),
            style="danger",
            icon_custom_emoji_id="5843843420468024653",
            callback_data=f"save_deck:{int(generated_deck_id)}",
        )
        if generated_deck_id is not None
        else None
    )

    if selected == DECK_BUTTON_LAYOUT_COPY_ONLY:
        rows = [[copy_button]]
    elif selected == DECK_BUTTON_LAYOUT_COMPACT:
        rows = (
            [[copy_button, favorite_button]]
            if favorite_button
            else [[copy_button]]
        )
    else:
        download_button = InlineKeyboardButton(
            text="Скачать",
            style="success",
            icon_custom_emoji_id="5879883461711367869",
            callback_data=f"open_pack:{download_key}",
        )
        actions = [download_button]
        if favorite_button:
            actions.append(favorite_button)
        rows = [[copy_button], actions]
    return InlineKeyboardMarkup(inline_keyboard=rows)
