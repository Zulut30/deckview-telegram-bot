#!/usr/bin/env python3
"""Verify that packaged modules and historical imports share one implementation."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


ALIASES = {
    "config": "deckview.config",
    "main": "deckview.bot.application",
    "web_app": "deckview.web.application",
    "dashboard": "deckview.web.dashboard",
    "web_db": "deckview.repositories.web",
    "deckview_queue": "deckview.workers.queue",
    "deckview_jobs": "deckview.workers.jobs",
    "deckview_worker": "deckview.workers.worker",
    "render_cache": "deckview.infrastructure.render_cache",
    "telegram_photo_cache": "deckview.infrastructure.telegram_photo_cache",
    "perf_telemetry": "deckview.infrastructure.perf_telemetry",
    "publish": "deckview.bot.publishing",
    "hsguru_fetch": "deckview.integrations.hsguru_fetch",
    "hsguru_meta": "deckview.integrations.hsguru_meta",
    "hsguru_archetype": "deckview.integrations.hsguru_archetype",
    "hsguru_import": "deckview.integrations.hsguru_import",
    "hs_data_api": "deckview.integrations.hs_data_api",
    "archetip": "deckview.services.archetype_service",
    "arena": "deckview.integrations.arena_stats",
    "bgs_comps": "deckview.integrations.battlegrounds_stats",
    "bot_health": "deckview.services.health_checks",
    "bot_security": "deckview.middlewares.flood_protection",
    "card_ratings": "deckview.repositories.card_ratings",
    "deck_buttons": "deckview.keyboards.deck_actions",
    "manacost_api": "deckview.integrations.manacost_api",
    "manacost_identity": "deckview.integrations.manacost_identity",
    "telegram_rich": "deckview.bot.rich",
    "threader": "deckview.infrastructure.async_tools",
    "wordpress": "deckview.integrations.wordpress",
}


def main() -> int:
    for legacy_name, package_name in ALIASES.items():
        legacy = importlib.import_module(legacy_name)
        packaged = importlib.import_module(package_name)
        if legacy is not packaged:
            raise AssertionError(
                f"{legacy_name} and {package_name} loaded separate implementations"
            )
    print(f"Modular import smoke test passed: {len(ALIASES)} aliases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
