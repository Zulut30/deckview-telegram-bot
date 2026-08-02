"""Health-report use case."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deckview.services.health_checks import format_health_message, run_health_checks

HealthRunner = Callable[..., dict[str, Any]]
HealthFormatter = Callable[[dict[str, Any]], str]


@dataclass(slots=True)
class HealthService:
    """Run blocking diagnostics outside the event loop and format a report."""

    started_at: float
    runner: HealthRunner = run_health_checks
    formatter: HealthFormatter = format_health_message

    async def report(self) -> str:
        uptime = max(0.0, time.monotonic() - self.started_at)
        data = await asyncio.to_thread(self.runner, uptime_seconds=uptime)
        return self.formatter(data)
