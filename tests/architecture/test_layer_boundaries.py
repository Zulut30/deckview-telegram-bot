from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "deckview"
PROJECT_ROOT = PACKAGE_ROOT.parent
RETIRED_ROOT_MODULES = {
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
    "web_db",
    "wordpress",
}
PRODUCTION_ROOTS = (
    PACKAGE_ROOT,
    PROJECT_ROOT / "image_creator",
    PROJECT_ROOT / "framework",
    PROJECT_ROOT / "db",
    PROJECT_ROOT / "tools",
    PROJECT_ROOT / "scripts",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def patched_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function_name = getattr(node.func, "id", None) or getattr(
            node.func, "attr", None
        )
        target = node.args[0]
        if (
            function_name == "patch"
            and isinstance(target, ast.Constant)
            and isinstance(target.value, str)
        ):
            modules.add(target.value.split(".", 1)[0])
    return modules


class LayerBoundaryTest(unittest.TestCase):
    def test_production_code_uses_packaged_modules_internally(self) -> None:
        for root in PRODUCTION_ROOTS:
            for path in root.rglob("*.py"):
                violations = sorted(imported_modules(path) & RETIRED_ROOT_MODULES)
                self.assertEqual(
                    [],
                    violations,
                    f"{path.relative_to(PROJECT_ROOT)}: {violations}",
                )

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

    def test_repository_root_has_no_python_modules(self) -> None:
        root_modules = sorted(path.name for path in PROJECT_ROOT.glob("*.py"))
        self.assertEqual([], root_modules)

    def test_runtime_entrypoints_live_in_the_package(self) -> None:
        for relative_path in (
            "__main__.py",
            "bot/application.py",
            "web/application.py",
            "workers/worker.py",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((PACKAGE_ROOT / relative_path).is_file())

    def test_mock_targets_do_not_use_retired_root_modules(self) -> None:
        for path in (PROJECT_ROOT / "tests").rglob("*.py"):
            violations = sorted(patched_modules(path) & RETIRED_ROOT_MODULES)
            self.assertEqual(
                [],
                violations,
                f"{path.relative_to(PROJECT_ROOT)}: {violations}",
            )

    def test_systemd_units_use_package_entrypoints(self) -> None:
        unit_dir = PROJECT_ROOT / "deploy" / "systemd"
        expected = {
            "deckview-bot.service": "python -m deckview",
            "deckview-web.service": "deckview.web.application:app",
            "deckview-worker.service": "python -m deckview.workers.worker",
        }
        for filename, entrypoint in expected.items():
            with self.subTest(filename=filename):
                source = (unit_dir / filename).read_text(encoding="utf-8")
                self.assertIn(entrypoint, source)
                self.assertNotIn(" main.py", source)
                self.assertNotIn(" web_app:app", source)


if __name__ == "__main__":
    unittest.main()
