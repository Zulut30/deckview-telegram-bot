from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendMessage,
    TelegramMethod,
)

from deckview.handlers.arena import create_arena_router
from deckview.handlers.battlegrounds import create_battlegrounds_router
from deckview.handlers.health import create_health_router
from deckview.services.arena_service import ArenaService
from deckview.services.battlegrounds_service import BattlegroundsService
from deckview.services.health_service import HealthService


class RecordingSession(BaseSession):
    """In-memory Telegram transport for real Dispatcher routing tests."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, GetMe):
            return types.User(
                id=9000,
                is_bot=True,
                first_name="Deckview",
                username="deckview_test_bot",
            )
        if isinstance(method, SendMessage):
            return types.Message(
                message_id=100 + len(self.calls),
                date=datetime.now(timezone.utc),
                chat=types.Chat(id=int(method.chat_id), type="private"),
                from_user=types.User(
                    id=9000,
                    is_bot=True,
                    first_name="Deckview",
                ),
                text=method.text,
            ).as_(bot)
        if isinstance(method, (EditMessageText, AnswerCallbackQuery)):
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65_536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:
            yield b""


def message_update(text: str, *, user_id: int = 7) -> types.Update:
    return types.Update(
        update_id=1,
        message=types.Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=types.Chat(id=42, type="private"),
            from_user=types.User(
                id=user_id,
                is_bot=False,
                first_name="Tester",
            ),
            text=text,
        ),
    )


def callback_update(data: str, *, user_id: int = 7) -> types.Update:
    return types.Update(
        update_id=2,
        callback_query=types.CallbackQuery(
            id="callback-1",
            chat_instance="chat-instance",
            from_user=types.User(
                id=user_id,
                is_bot=False,
                first_name="Tester",
            ),
            data=data,
            message=types.Message(
                message_id=2,
                date=datetime.now(timezone.utc),
                chat=types.Chat(id=42, type="private"),
                text="Old text",
            ),
        ),
    )


class ModularRoutingIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = RecordingSession()
        self.bot = Bot(
            "42:TEST",
            session=self.session,
        )

    async def asyncTearDown(self) -> None:
        await self.bot.session.close()

    async def test_arena_command_reaches_injected_service(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.include_router(
            create_arena_router(
                ArenaService(
                    loader=lambda: {"ok": True},
                    formatter=lambda data, period: f"ARENA:{period}:{data['ok']}",
                )
            )
        )

        await dispatcher.feed_update(self.bot, message_update("/arena"))

        messages = [call for call in self.session.calls if isinstance(call, SendMessage)]
        self.assertEqual(["ARENA:hsreplay:True"], [call.text for call in messages])

    async def test_legacy_arena_callback_is_answered_before_loading(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.include_router(
            create_arena_router(
                ArenaService(loader=lambda: {}, formatter=lambda data, period: "ARENA")
            )
        )

        await dispatcher.feed_update(
            self.bot,
            callback_update("arena_view:matrix"),
        )

        calls = [type(call) for call in self.session.calls]
        self.assertEqual([AnswerCallbackQuery, EditMessageText], calls)

    async def test_battlegrounds_command_sends_loading_then_result(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.include_router(
            create_battlegrounds_router(
                BattlegroundsService(
                    loader=lambda period: {"period": period},
                    formatter=lambda data, period: f"BGS:{data['period']}",
                )
            )
        )

        await dispatcher.feed_update(self.bot, message_update("/comps"))

        self.assertEqual([SendMessage, EditMessageText], [type(c) for c in self.session.calls])
        self.assertEqual("BGS:last-patch", self.session.calls[-1].text)

    async def test_health_command_preserves_admin_boundary(self) -> None:
        service = HealthService(
            started_at=0.0,
            runner=lambda **kwargs: {"uptime": kwargs["uptime_seconds"]},
            formatter=lambda data: "HEALTH:OK",
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(
            create_health_router(service=service, is_admin=lambda user_id: user_id == 7)
        )

        await dispatcher.feed_update(self.bot, message_update("/health", user_id=8))
        await dispatcher.feed_update(self.bot, message_update("/health", user_id=7))

        calls = [(type(call), getattr(call, "text", None)) for call in self.session.calls]
        self.assertEqual(
            [
                (SendMessage, "⛔ Команда доступна только администраторам."),
                (SendMessage, "⏳ <b>Проверяю источники…</b>"),
                (EditMessageText, "HEALTH:OK"),
            ],
            calls,
        )


if __name__ == "__main__":
    unittest.main()
