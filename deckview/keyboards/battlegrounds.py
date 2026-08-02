"""Battlegrounds navigation keyboards."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PERIOD_BUTTONS: tuple[tuple[str, str], ...] = (
    ("last-patch", "⚡ Патч"),
    ("past-seven", "📅 7 дней"),
    ("past-three", "🗓️ 3 дня"),
)


def battlegrounds_period_keyboard(active: str) -> InlineKeyboardMarkup:
    """Build a stable, scannable period selector."""

    buttons = [
        InlineKeyboardButton(
            text=("✅ " if active == period else "") + label,
            callback_data=f"comps_period:{period}",
        )
        for period, label in _PERIOD_BUTTONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
