from __future__ import annotations

import asyncio
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Deque, Hashable

from aiogram import BaseMiddleware, types


_DECK_CODE_RE = re.compile(r"(?:^|\s)AA[A-Za-z0-9+/=_-]{8,}")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class FloodProtectionConfig:
    enabled: bool = True
    global_limit: int = 1_000
    global_window_seconds: int = 10
    user_limit: int = 30
    user_window_seconds: int = 10
    chat_limit: int = 120
    chat_window_seconds: int = 10
    render_limit: int = 3
    render_window_seconds: int = 60
    global_render_limit: int = 60
    global_render_window_seconds: int = 60
    notification_window_seconds: int = 30
    max_tracked_keys: int = 20_000

    @classmethod
    def from_env(cls) -> "FloodProtectionConfig":
        enabled = os.getenv("DECKVIEW_FLOOD_PROTECTION", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            enabled=enabled,
            global_limit=_env_int(
                "DECKVIEW_RATE_GLOBAL_LIMIT", 1_000, minimum=10, maximum=100_000
            ),
            global_window_seconds=_env_int(
                "DECKVIEW_RATE_GLOBAL_WINDOW", 10, minimum=1, maximum=3_600
            ),
            user_limit=_env_int(
                "DECKVIEW_RATE_USER_LIMIT", 30, minimum=1, maximum=1_000
            ),
            user_window_seconds=_env_int(
                "DECKVIEW_RATE_USER_WINDOW", 10, minimum=1, maximum=3_600
            ),
            chat_limit=_env_int(
                "DECKVIEW_RATE_CHAT_LIMIT", 120, minimum=1, maximum=10_000
            ),
            chat_window_seconds=_env_int(
                "DECKVIEW_RATE_CHAT_WINDOW", 10, minimum=1, maximum=3_600
            ),
            render_limit=_env_int(
                "DECKVIEW_RATE_RENDER_LIMIT", 3, minimum=1, maximum=100
            ),
            render_window_seconds=_env_int(
                "DECKVIEW_RATE_RENDER_WINDOW", 60, minimum=1, maximum=86_400
            ),
            global_render_limit=_env_int(
                "DECKVIEW_RATE_GLOBAL_RENDER_LIMIT",
                60,
                minimum=1,
                maximum=10_000,
            ),
            global_render_window_seconds=_env_int(
                "DECKVIEW_RATE_GLOBAL_RENDER_WINDOW",
                60,
                minimum=1,
                maximum=86_400,
            ),
            notification_window_seconds=_env_int(
                "DECKVIEW_RATE_NOTICE_WINDOW", 30, minimum=1, maximum=3_600
            ),
            max_tracked_keys=_env_int(
                "DECKVIEW_RATE_MAX_KEYS", 20_000, minimum=100, maximum=1_000_000
            ),
        )


class SlidingWindowRateLimiter:
    """Small in-process limiter for the single Telegram update consumer."""

    def __init__(
        self,
        *,
        max_keys: int = 20_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._events: dict[Hashable, Deque[float]] = defaultdict(deque)
        self._max_keys = max(100, int(max_keys))
        self._clock = clock
        self._lock = asyncio.Lock()

    async def allow(
        self,
        key: Hashable,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, float]:
        now = self._clock()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(0.0, events[0] + window_seconds - now)
                return False, retry_after
            events.append(now)
            if len(self._events) > self._max_keys:
                self._prune(cutoff)
            return True, 0.0

    def _prune(self, cutoff: float) -> None:
        stale = [
            key
            for key, events in self._events.items()
            if not events or events[-1] <= cutoff
        ]
        for key in stale:
            self._events.pop(key, None)
        overflow = len(self._events) - self._max_keys
        if overflow > 0:
            oldest = sorted(
                self._events,
                key=lambda key: self._events[key][-1] if self._events[key] else 0.0,
            )
            for key in oldest[:overflow]:
                self._events.pop(key, None)


def _event_identity(event: object) -> tuple[int | None, int | None]:
    user = getattr(event, "from_user", None)
    chat = getattr(event, "chat", None)
    if isinstance(event, types.CallbackQuery):
        chat = getattr(getattr(event, "message", None), "chat", None)
    user_id = getattr(user, "id", None)
    chat_id = getattr(chat, "id", None)
    return user_id, chat_id


def _is_render_request(event: object) -> bool:
    text = str(getattr(event, "text", "") or "")
    return bool(_DECK_CODE_RE.search(text[:8_192]))


class TelegramFloodProtectionMiddleware(BaseMiddleware):
    def __init__(
        self,
        config: FloodProtectionConfig | None = None,
        limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.config = config or FloodProtectionConfig.from_env()
        self.limiter = limiter or SlidingWindowRateLimiter(
            max_keys=self.config.max_tracked_keys
        )

    async def __call__(
        self,
        handler: Callable[[object, dict], Awaitable[object]],
        event: object,
        data: dict,
    ) -> object | None:
        if not self.config.enabled:
            return await handler(event, data)

        user_id, chat_id = _event_identity(event)
        actor = user_id if user_id is not None else chat_id
        if actor is None:
            return await handler(event, data)

        allowed, retry_after = await self.limiter.allow(
            ("user", actor),
            limit=self.config.user_limit,
            window_seconds=self.config.user_window_seconds,
        )
        if allowed and chat_id is not None:
            allowed, retry_after = await self.limiter.allow(
                ("chat", chat_id),
                limit=self.config.chat_limit,
                window_seconds=self.config.chat_window_seconds,
            )
        if allowed:
            allowed, retry_after = await self.limiter.allow(
                ("global",),
                limit=self.config.global_limit,
                window_seconds=self.config.global_window_seconds,
            )
        if allowed and _is_render_request(event):
            allowed, retry_after = await self.limiter.allow(
                ("render", actor),
                limit=self.config.render_limit,
                window_seconds=self.config.render_window_seconds,
            )
            if allowed:
                allowed, retry_after = await self.limiter.allow(
                    ("global-render",),
                    limit=self.config.global_render_limit,
                    window_seconds=self.config.global_render_window_seconds,
                )
        if allowed:
            return await handler(event, data)

        await self._notify_once(event, actor, retry_after)
        should_log, _ = await self.limiter.allow(
            ("security-log",), limit=1, window_seconds=5
        )
        if should_log:
            print(
                "[Deckview security] update throttled "
                f"event={type(event).__name__} retry_after={retry_after:.1f}s"
            )
        return None

    async def _notify_once(
        self,
        event: object,
        actor: int,
        retry_after: float,
    ) -> None:
        should_notify, _ = await self.limiter.allow(
            ("notice", actor),
            limit=1,
            window_seconds=self.config.notification_window_seconds,
        )
        if not should_notify:
            return
        wait_seconds = max(1, int(retry_after + 0.999))
        text = f"Слишком много запросов. Попробуйте через {wait_seconds} сек."
        try:
            if isinstance(event, types.CallbackQuery):
                await event.answer(text)
            elif isinstance(event, types.Message):
                await event.answer(text)
        except Exception:
            # A protection response must never break update processing.
            pass
