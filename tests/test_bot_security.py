import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import types

from deckview.middlewares.flood_protection import (
    FloodProtectionConfig,
    SlidingWindowRateLimiter,
    TelegramFloodProtectionMiddleware,
    _is_render_request,
)


class SlidingWindowRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_until_oldest_event_leaves_window(self):
        now = [100.0]
        limiter = SlidingWindowRateLimiter(clock=lambda: now[0])

        self.assertEqual(
            await limiter.allow("u", limit=2, window_seconds=10), (True, 0.0)
        )
        self.assertEqual(
            await limiter.allow("u", limit=2, window_seconds=10), (True, 0.0)
        )
        allowed, retry_after = await limiter.allow(
            "u", limit=2, window_seconds=10
        )
        self.assertFalse(allowed)
        self.assertEqual(retry_after, 10.0)

        now[0] = 110.01
        self.assertEqual(
            await limiter.allow("u", limit=2, window_seconds=10), (True, 0.0)
        )

    async def test_keys_are_isolated(self):
        limiter = SlidingWindowRateLimiter(clock=lambda: 100.0)
        self.assertEqual(
            await limiter.allow("u1", limit=1, window_seconds=10), (True, 0.0)
        )
        self.assertEqual(
            await limiter.allow("u2", limit=1, window_seconds=10), (True, 0.0)
        )


class FloodProtectionConfigTests(unittest.TestCase):
    def test_invalid_and_extreme_values_are_safely_bounded(self):
        with patch.dict(
            os.environ,
            {
                "DECKVIEW_RATE_USER_LIMIT": "invalid",
                "DECKVIEW_RATE_CHAT_LIMIT": "999999",
                "DECKVIEW_RATE_RENDER_LIMIT": "0",
            },
            clear=True,
        ):
            config = FloodProtectionConfig.from_env()
        self.assertEqual(config.user_limit, 30)
        self.assertEqual(config.chat_limit, 10_000)
        self.assertEqual(config.render_limit, 1)

    def test_deck_code_detection_is_bounded_and_specific(self):
        event = type("Event", (), {"text": "name AAEC0123456789"})()
        self.assertTrue(_is_render_request(event))
        event.text = "AARDVARK is not a deck code"
        self.assertFalse(_is_render_request(event))


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _message(text: str) -> types.Message:
        return types.Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=types.Chat(id=9, type="private"),
            from_user=types.User(id=7, is_bot=False, first_name="Tester"),
            text=text,
        )

    async def test_flooded_message_does_not_reach_handler(self):
        config = FloodProtectionConfig(
            user_limit=1,
            user_window_seconds=60,
            chat_limit=10,
            chat_window_seconds=60,
            render_limit=10,
            render_window_seconds=60,
        )
        middleware = TelegramFloodProtectionMiddleware(config=config)
        event = self._message("hello")
        handler = AsyncMock(return_value="ok")

        self.assertEqual(await middleware(handler, event, {}), "ok")
        self.assertIsNone(await middleware(handler, event, {}))
        handler.assert_awaited_once()

    async def test_render_limit_is_separate_from_general_limit(self):
        config = FloodProtectionConfig(
            user_limit=10,
            user_window_seconds=60,
            chat_limit=10,
            chat_window_seconds=60,
            render_limit=1,
            render_window_seconds=60,
        )
        middleware = TelegramFloodProtectionMiddleware(config=config)
        event = self._message("AAEC0123456789")
        handler = AsyncMock(return_value="ok")

        self.assertEqual(await middleware(handler, event, {}), "ok")
        self.assertIsNone(await middleware(handler, event, {}))
        handler.assert_awaited_once()

    async def test_default_limit_accepts_four_decks_as_queueable_burst(self):
        middleware = TelegramFloodProtectionMiddleware(
            config=FloodProtectionConfig()
        )
        handler = AsyncMock(return_value="queued")

        for index in range(4):
            event = self._message(f"AAEC012345678{index}")
            self.assertEqual(await middleware(handler, event, {}), "queued")

        self.assertEqual(handler.await_count, 4)

    async def test_global_render_limit_stops_distributed_flood(self):
        config = FloodProtectionConfig(
            global_limit=100,
            global_window_seconds=60,
            user_limit=100,
            user_window_seconds=60,
            chat_limit=100,
            chat_window_seconds=60,
            render_limit=100,
            render_window_seconds=60,
            global_render_limit=1,
            global_render_window_seconds=60,
        )
        middleware = TelegramFloodProtectionMiddleware(config=config)
        first = self._message("AAEC0123456789")
        second = types.Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=types.Chat(id=19, type="private"),
            from_user=types.User(id=17, is_bot=False, first_name="Other"),
            text="AAEC9876543210",
        )
        handler = AsyncMock(return_value="ok")

        self.assertEqual(await middleware(handler, first, {}), "ok")
        self.assertIsNone(await middleware(handler, second, {}))
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
