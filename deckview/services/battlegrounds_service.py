"""Battlegrounds use cases independent from Telegram update types."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deckview.integrations.battlegrounds_stats import (
    PERIOD_LABEL,
    format_comps_message,
    get_bgs_comps,
)

BattlegroundsLoader = Callable[[str], dict[str, Any]]
BattlegroundsFormatter = Callable[[dict[str, Any], str], str]


@dataclass(slots=True)
class BattlegroundsService:
    """Load and format Battlegrounds composition statistics."""

    loader: BattlegroundsLoader = get_bgs_comps
    formatter: BattlegroundsFormatter = format_comps_message

    @property
    def periods(self) -> tuple[str, ...]:
        return tuple(PERIOD_LABEL)

    async def overview(self, period: str = "last-patch") -> str:
        if period not in PERIOD_LABEL:
            raise ValueError(f"Unsupported Battlegrounds period: {period}")
        data = await asyncio.to_thread(self.loader, period)
        return self.formatter(data, period)
