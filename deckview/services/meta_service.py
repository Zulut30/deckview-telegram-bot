"""Business rules for the Hearthstone meta view."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


META_SOURCE_LIMIT = 500
META_VIEW_LIMIT = 10


def _finite_number(value: object, *, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def rank_meta_by_winrate(
    archetypes: Iterable[dict[str, Any]],
    *,
    limit: int = META_VIEW_LIMIT,
) -> list[dict[str, Any]]:
    """Return a deterministic top list ordered by win rate, not popularity."""
    indexed = list(enumerate(archetypes))

    def sort_key(
        entry: tuple[int, dict[str, Any]],
    ) -> tuple[float, int, float, int]:
        source_index, archetype = entry
        metrics = archetype.get("metrics") or {}
        winrate = _finite_number(metrics.get("winratePercent"), fallback=-math.inf)
        games = int(_finite_number(metrics.get("games"), fallback=0.0))
        popularity = _finite_number(metrics.get("popularityPercent"), fallback=0.0)
        return winrate, games, popularity, -source_index

    safe_limit = max(0, int(limit))
    return [
        archetype
        for _, archetype in sorted(indexed, key=sort_key, reverse=True)[:safe_limit]
    ]


__all__ = ["META_SOURCE_LIMIT", "META_VIEW_LIMIT", "rank_meta_by_winrate"]
