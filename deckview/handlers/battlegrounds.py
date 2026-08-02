"""Telegram-facing Battlegrounds handlers."""

from __future__ import annotations

import html

from aiogram import F, Router, types
from aiogram.filters import Command

from deckview.keyboards.battlegrounds import battlegrounds_period_keyboard
from deckview.services.battlegrounds_service import BattlegroundsService


def create_battlegrounds_router(
    service: BattlegroundsService | None = None,
) -> Router:
    """Create an isolated Battlegrounds router with an injectable service."""

    battlegrounds_service = service or BattlegroundsService()
    router = Router(name="battlegrounds")

    async def send_overview(message: types.Message) -> None:
        status = await message.answer("⏳ <b>Загружаю данные Полей сражений…</b>")
        try:
            text = await battlegrounds_service.overview("last-patch")
            await status.edit_text(
                text,
                reply_markup=battlegrounds_period_keyboard("last-patch"),
            )
        except Exception as exc:
            await status.edit_text(
                f"❌ Не удалось загрузить данные: {html.escape(str(exc)[:300])}"
            )

    @router.message(Command("comps"))
    async def battlegrounds_command(message: types.Message) -> None:
        await send_overview(message)

    @router.message(F.text == "🎮 Поля сражений")
    async def battlegrounds_menu_button(message: types.Message) -> None:
        await send_overview(message)

    @router.callback_query(F.data.startswith("comps_period:"))
    async def battlegrounds_period(callback: types.CallbackQuery) -> None:
        period = callback.data.split(":", 1)[1]
        if period not in battlegrounds_service.periods:
            await callback.answer("Неверный период.", show_alert=True)
            return
        await callback.answer()
        try:
            text = await battlegrounds_service.overview(period)
            await callback.message.edit_text(
                text,
                reply_markup=battlegrounds_period_keyboard(period),
            )
        except Exception as exc:
            await callback.message.edit_text(
                f"❌ Ошибка: {html.escape(str(exc)[:300])}"
            )

    return router
