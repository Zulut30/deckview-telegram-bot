"""Telegram-facing Arena handlers."""

from __future__ import annotations

import html

from aiogram import F, Router, types
from aiogram.filters import Command

from deckview.services.arena_service import ArenaService


def create_arena_router(service: ArenaService | None = None) -> Router:
    """Create an isolated Arena router with an injectable service."""

    arena_service = service or ArenaService()
    router = Router(name="arena")

    async def send_overview(message: types.Message) -> None:
        try:
            await message.answer(await arena_service.current_overview())
        except Exception as exc:
            await message.answer(
                f"❌ Не удалось загрузить данные арены: {html.escape(str(exc)[:300])}"
            )

    @router.message(Command("arena"))
    async def arena_command(message: types.Message) -> None:
        await send_overview(message)

    @router.message(F.text == "🏟️ Арена")
    async def arena_menu_button(message: types.Message) -> None:
        await send_overview(message)

    @router.callback_query(F.data.startswith("arena_view:"))
    async def arena_legacy_view(callback: types.CallbackQuery) -> None:
        await callback.answer("Матрица больше не используется.")
        try:
            await callback.message.edit_text(await arena_service.current_overview())
        except Exception as exc:
            await callback.message.edit_text(
                f"❌ Ошибка: {html.escape(str(exc)[:300])}"
            )

    @router.callback_query(F.data.startswith("arena_period:"))
    async def arena_legacy_period(callback: types.CallbackQuery) -> None:
        await callback.answer("Показываю актуальные данные Арены.")
        try:
            await callback.message.edit_text(await arena_service.current_overview())
        except Exception as exc:
            await callback.message.edit_text(
                f"❌ Ошибка: {html.escape(str(exc)[:300])}"
            )

    return router
