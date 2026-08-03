#!/usr/bin/env python3
"""Smoke-test canonical Deckview package imports and the clean repository root."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MODULES = (
    "deckview.__main__",
    "deckview.config",
    "deckview.bot.application",
    "deckview.bot.publishing",
    "deckview.bot.rich",
    "deckview.handlers.arena",
    "deckview.handlers.battlegrounds",
    "deckview.handlers.health",
    "deckview.infrastructure.async_tools",
    "deckview.infrastructure.perf_telemetry",
    "deckview.infrastructure.render_cache",
    "deckview.infrastructure.telegram_photo_cache",
    "deckview.integrations.arena_stats",
    "deckview.integrations.battlegrounds_stats",
    "deckview.integrations.hs_data_api",
    "deckview.integrations.hsguru_archetype",
    "deckview.integrations.hsguru_fetch",
    "deckview.integrations.hsguru_import",
    "deckview.integrations.hsguru_meta",
    "deckview.integrations.manacost_api",
    "deckview.integrations.manacost_identity",
    "deckview.integrations.wordpress",
    "deckview.keyboards.deck_actions",
    "deckview.middlewares.flood_protection",
    "deckview.repositories.card_ratings",
    "deckview.repositories.web",
    "deckview.services.archetype_service",
    "deckview.services.health_checks",
    "deckview.web.application",
    "deckview.web.dashboard",
    "deckview.workers.jobs",
    "deckview.workers.queue",
    "deckview.workers.worker",
)


def main() -> int:
    root_modules = sorted(PROJECT_ROOT.glob("*.py"))
    if root_modules:
        names = ", ".join(path.name for path in root_modules)
        raise AssertionError(f"Python modules must not live at repository root: {names}")
    for module_name in MODULES:
        importlib.import_module(module_name)
    print(f"Package import smoke test passed: {len(MODULES)} canonical modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
