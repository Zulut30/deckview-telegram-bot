"""Telegram-facing health diagnostics handler."""

from __future__ import annotations

import html
from collections.abc import Callable

from aiogram import Router, types
from aiogram.filters import Command

from deckview.services.health_service import HealthService

AdminPredicate = Callable[[int], bool]


def create_health_router(
    *,
    service: HealthService,
    is_admin: AdminPredicate,
) -> Router:
    """Create the admin-only health router."""

    router = Router(name="health")

    @router.message(Command("healt", "health"))
    async def health_command(message: types.Message) -> None:
        if not message.from_user or not is_admin(message.from_user.id):
            await message.answer("⛔ Команда доступна только администраторам.")
            return
        status = await message.answer("⏳ <b>Проверяю источники…</b>")
        try:
            await status.edit_text(await service.report())
        except Exception as exc:
            await status.edit_text(
                f"❌ Health check failed: {html.escape(str(exc)[:300])}"
            )

    return router
