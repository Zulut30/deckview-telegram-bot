from __future__ import annotations

import importlib
import unittest

from deckview.bot.routers import create_modular_router
from deckview.services.arena_service import ArenaService
from deckview.services.battlegrounds_service import BattlegroundsService


class ModularRouterTest(unittest.TestCase):
    def test_composition_root_registers_named_feature_routers(self) -> None:
        router = create_modular_router()
        self.assertEqual("deckview", router.name)
        self.assertEqual(
            ["arena", "battlegrounds", "health"],
            [child.name for child in router.sub_routers],
        )
        self.assertEqual({"message", "callback_query"}, set(router.resolve_used_update_types()))

    def test_legacy_modules_are_identity_preserving_aliases(self) -> None:
        aliases = {
            "archetip": "deckview.services.archetype_service",
            "arena": "deckview.integrations.arena_stats",
            "bgs_comps": "deckview.integrations.battlegrounds_stats",
            "bot_health": "deckview.services.health_checks",
            "bot_security": "deckview.middlewares.flood_protection",
            "deck_buttons": "deckview.keyboards.deck_actions",
            "deckview_jobs": "deckview.workers.jobs",
            "deckview_queue": "deckview.workers.queue",
            "deckview_worker": "deckview.workers.worker",
            "dashboard": "deckview.web.dashboard",
            "hs_data_api": "deckview.integrations.hs_data_api",
            "hsguru_archetype": "deckview.integrations.hsguru_archetype",
            "hsguru_fetch": "deckview.integrations.hsguru_fetch",
            "hsguru_import": "deckview.integrations.hsguru_import",
            "hsguru_meta": "deckview.integrations.hsguru_meta",
            "manacost_api": "deckview.integrations.manacost_api",
            "manacost_identity": "deckview.integrations.manacost_identity",
            "card_ratings": "deckview.repositories.card_ratings",
            "config": "deckview.config",
            "perf_telemetry": "deckview.infrastructure.perf_telemetry",
            "publish": "deckview.bot.publishing",
            "render_cache": "deckview.infrastructure.render_cache",
            "telegram_photo_cache": "deckview.infrastructure.telegram_photo_cache",
            "telegram_rich": "deckview.bot.rich",
            "threader": "deckview.infrastructure.async_tools",
            "web_app": "deckview.web.application",
            "web_db": "deckview.repositories.web",
            "wordpress": "deckview.integrations.wordpress",
        }
        for legacy_name, package_name in aliases.items():
            with self.subTest(legacy_name=legacy_name):
                self.assertIs(
                    importlib.import_module(legacy_name),
                    importlib.import_module(package_name),
                )


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_arena_service_is_injectable_without_telegram(self) -> None:
        service = ArenaService(
            loader=lambda: {"stats": []},
            formatter=lambda data, period: f"{period}:{len(data['stats'])}",
        )
        self.assertEqual("hsreplay:0", await service.current_overview())

    async def test_battlegrounds_service_validates_period(self) -> None:
        service = BattlegroundsService(
            loader=lambda period: {"period": period},
            formatter=lambda data, period: data["period"],
        )
        self.assertEqual("past-seven", await service.overview("past-seven"))
        with self.assertRaises(ValueError):
            await service.overview("unknown")


if __name__ == "__main__":
    unittest.main()
