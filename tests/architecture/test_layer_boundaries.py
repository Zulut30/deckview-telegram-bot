from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "deckview"
PROJECT_ROOT = PACKAGE_ROOT.parent
LEGACY_ALIAS_MODULES = {
    "archetip",
    "arena",
    "bgs_comps",
    "bot_health",
    "bot_security",
    "card_ratings",
    "config",
    "deck_buttons",
    "deckview_jobs",
    "deckview_queue",
    "deckview_worker",
    "dashboard",
    "hs_data_api",
    "hsguru_archetype",
    "hsguru_fetch",
    "hsguru_import",
    "hsguru_meta",
    "manacost_api",
    "manacost_identity",
    "perf_telemetry",
    "publish",
    "render_cache",
    "telegram_photo_cache",
    "telegram_rich",
    "threader",
    "web_app",
    "web_db",
    "wordpress",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class LayerBoundaryTest(unittest.TestCase):
    def test_production_code_uses_packaged_modules_internally(self) -> None:
        excluded_files = {f"{name}.py" for name in LEGACY_ALIAS_MODULES}
        for path in PROJECT_ROOT.glob("*.py"):
            if path.name in excluded_files or path.name.startswith("test_"):
                continue
            violations = sorted(imported_modules(path) & LEGACY_ALIAS_MODULES)
            self.assertEqual([], violations, f"{path.name}: {violations}")

    def test_handlers_only_reach_services_and_keyboards(self) -> None:
        forbidden = ("deckview.integrations", "deckview.repositories")
        for path in (PACKAGE_ROOT / "handlers").glob("*.py"):
            imports = imported_modules(path)
            violations = sorted(
                module for module in imports if module.startswith(forbidden)
            )
            self.assertEqual([], violations, f"{path.name}: {violations}")

    def test_services_do_not_depend_on_telegram_transport(self) -> None:
        for path in (PACKAGE_ROOT / "services").glob("*.py"):
            imports = imported_modules(path)
            violations = sorted(
                module
                for module in imports
                if module == "aiogram"
                or module.startswith("aiogram.")
                or module.startswith("deckview.handlers")
            )
            self.assertEqual([], violations, f"{path.name}: {violations}")

    def test_integrations_do_not_depend_on_handlers(self) -> None:
        for path in (PACKAGE_ROOT / "integrations").glob("*.py"):
            imports = imported_modules(path)
            violations = sorted(
                module
                for module in imports
                if module.startswith("aiogram")
                or module.startswith("deckview.handlers")
            )
            self.assertEqual([], violations, f"{path.name}: {violations}")

    def test_repositories_do_not_depend_on_telegram_or_integrations(self) -> None:
        for path in (PACKAGE_ROOT / "repositories").glob("*.py"):
            imports = imported_modules(path)
            violations = sorted(
                module
                for module in imports
                if module.startswith("aiogram")
                or module.startswith("deckview.handlers")
                or module.startswith("deckview.integrations")
            )
            self.assertEqual([], violations, f"{path.name}: {violations}")

    def test_main_composes_modular_router_without_duplicate_migrated_handlers(self) -> None:
        source = (PACKAGE_ROOT / "bot" / "application.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from deckview.bot.routers import create_modular_router", source)
        self.assertLess(
            source.index("dp.include_router(\n        create_modular_router"),
            source.index("dp.include_router(router)"),
        )
        forbidden_handler_markers = (
            '@router.message(Command("arena"))',
            '@router.message(Command("comps"))',
            '@router.message(Command("healt", "health"))',
            '@router.callback_query(F.data.startswith("arena_view:"))',
            '@router.callback_query(F.data.startswith("arena_period:"))',
            '@router.callback_query(F.data.startswith("comps_period:"))',
        )
        for marker in forbidden_handler_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)

    def test_root_entrypoints_are_thin_compatibility_modules(self) -> None:
        for module_name in LEGACY_ALIAS_MODULES | {"main"}:
            path = PROJECT_ROOT / f"{module_name}.py"
            with self.subTest(module_name=module_name):
                self.assertTrue(path.is_file())
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    25,
                    f"{module_name}.py contains implementation instead of a package alias",
                )


if __name__ == "__main__":
    unittest.main()
