"""Arena use cases independent from Telegram update types."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deckview.integrations.arena_stats import format_arena_message, get_arena_stats

ArenaLoader = Callable[[], dict[str, Any]]
ArenaFormatter = Callable[[dict[str, Any], str], str]


@dataclass(slots=True)
class ArenaService:
    """Load and format the current arena overview."""

    loader: ArenaLoader = get_arena_stats
    formatter: ArenaFormatter = format_arena_message

    async def current_overview(self) -> str:
        data = await asyncio.to_thread(self.loader)
        return self.formatter(data, "hsreplay")
