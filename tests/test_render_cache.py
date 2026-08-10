"""Tests for the fail-open, versioned 21-day render cache."""

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from deckview.infrastructure.render_cache import (
    attach_render_preview,
    build_render_cache_key,
    lookup_render_cache_by_key,
    lookup_render_cache,
    materialize_render_cache,
    store_render_cache,
)


class RenderCacheTests(unittest.TestCase):
    def test_key_is_stable_and_versioned(self):
        with patch.dict(os.environ, {}, clear=False):
            first = build_render_cache_key(" code ", " Name ")
            second = build_render_cache_key("code", "Name")
        self.assertEqual(first, second)
        with patch.dict(os.environ, {"DECKVIEW_TEMPLATE_VERSION": "next"}):
            changed = build_render_cache_key("code", "Name")
        self.assertNotEqual(first, changed)

    def test_card_asset_revision_changes_cache_key(self):
        with patch.dict(
            os.environ,
            {"DECKVIEW_CARD_ASSET_VERSION": "arena-image-v2"},
        ):
            current = build_render_cache_key("code", "Name")
        with patch.dict(
            os.environ,
            {"DECKVIEW_CARD_ASSET_VERSION": "arena-image-v3"},
        ):
            changed = build_render_cache_key("code", "Name")
        self.assertNotEqual(current, changed)

    def test_parchment_style_has_its_own_cache_key(self):
        classic = build_render_cache_key("code", "Name")
        explicit_classic = build_render_cache_key("code", "Name", "classic")
        parchment = build_render_cache_key("code", "Name", "parchment")
        self.assertEqual(classic, explicit_classic)
        self.assertNotEqual(classic, parchment)

    def test_custom_theme_revision_changes_cache_key(self):
        first = build_render_cache_key("code", "Name", "custom:user:42:1")
        second = build_render_cache_key("code", "Name", "custom:user:42:2")
        self.assertNotEqual(first, second)

    def test_font_choice_changes_cache_key(self):
        automatic = build_render_cache_key("code", "Name", "classic")
        oswald = build_render_cache_key("code", "Name", "classic:font:oswald")
        self.assertNotEqual(automatic, oswald)

    def test_title_size_changes_cache_key(self):
        normal = build_render_cache_key("code", "Name", "classic")
        large = build_render_cache_key("code", "Name", "classic:text:large")
        self.assertNotEqual(normal, large)

    def test_write_then_read_and_missing_file_fails_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "cache.db"
            cache_root = root / "render-cache"
            source = root / "source.jpg"
            Image.new("RGB", (64, 64), (20, 30, 40)).save(source, "JPEG")
            environment = {
                "WEB_DATABASE_PATH": str(database),
                "DECKVIEW_RENDER_CACHE_ROOT": str(cache_root),
                "DECKVIEW_RENDER_CACHE_WRITE": "1",
                "DECKVIEW_RENDER_CACHE_READ": "1",
                "DECKVIEW_RENDER_CACHE_TTL_HOURS": "504",
            }
            with patch.dict(os.environ, environment):
                stored = store_render_cache(
                    deck_code="code",
                    deck_name="Name",
                    source_path=source,
                    cost=100,
                    deck_class="Маг",
                    deck_mode="Стандарт",
                    card_dbf_ids=[1, 2],
                )
                hit = lookup_render_cache("code", "Name")
                materialized = root / "request" / "deck.jpg"
                copied = materialize_render_cache(hit, materialized)
                artifact_mode = Path(stored["artifact_path"]).stat().st_mode & 0o777
                Path(stored["artifact_path"]).unlink()
                missing = lookup_render_cache("code", "Name")

            self.assertIsNotNone(stored)
            self.assertEqual(hit["cost"], 100)
            self.assertEqual(hit["card_dbf_ids"], [1, 2])
            self.assertEqual(hit["cache_layer"], "memory")
            self.assertEqual(copied, str(materialized))
            self.assertEqual(materialized.read_bytes(), source.read_bytes())
            self.assertEqual(artifact_mode, 0o644)
            self.assertIsNone(missing)
            with sqlite3.connect(database) as conn:
                ttl = conn.execute(
                    "SELECT (julianday(expires_at) - julianday(created_at)) * 24 FROM render_cache"
                ).fetchone()[0]
            self.assertAlmostEqual(ttl, 504, places=3)

    def test_lookup_by_cache_key_keeps_download_available_after_temp_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            Image.new("RGB", (64, 64), (20, 30, 40)).save(source, "JPEG")
            environment = {
                "WEB_DATABASE_PATH": str(root / "cache.db"),
                "DECKVIEW_RENDER_CACHE_ROOT": str(root / "render-cache"),
                "DECKVIEW_RENDER_CACHE_WRITE": "1",
                "DECKVIEW_RENDER_CACHE_READ": "1",
            }
            with patch.dict(os.environ, environment):
                stored = store_render_cache(
                    deck_code="download-code",
                    deck_name="Download",
                    source_path=source,
                    cost=100,
                    deck_class="Маг",
                    deck_mode="Стандарт",
                    card_dbf_ids=[1, 2],
                )
                hit = lookup_render_cache_by_key(stored["cache_key"])
                invalid = lookup_render_cache_by_key("../escape")

            self.assertIsNotNone(hit)
            self.assertEqual(hit["artifact_path"], stored["artifact_path"])
            self.assertEqual(hit["cache_key"], stored["cache_key"])
            self.assertIsNone(invalid)

    def test_disabled_write_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (64, 64), (20, 30, 40)).save(source, "JPEG")
            with patch.dict(os.environ, {"DECKVIEW_RENDER_CACHE_WRITE": "0"}):
                stored = store_render_cache(
                    deck_code="code",
                    deck_name=None,
                    source_path=source,
                    cost=0,
                    deck_class=None,
                    deck_mode=None,
                    card_dbf_ids=[],
                )
            self.assertIsNone(stored)

    def test_public_artifact_path_survives_restrictive_service_umask(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "render-cache"
            source = root / "source.jpg"
            Image.new("RGB", (64, 64), (20, 30, 40)).save(source, "JPEG")
            environment = {
                "WEB_DATABASE_PATH": str(root / "cache.db"),
                "DECKVIEW_RENDER_CACHE_ROOT": str(cache_root),
                "DECKVIEW_RENDER_CACHE_WRITE": "1",
                "DECKVIEW_RENDER_CACHE_READ": "1",
            }
            previous_umask = os.umask(0o027)
            try:
                with patch.dict(os.environ, environment):
                    stored = store_render_cache(
                        deck_code="restricted-code",
                        deck_name="Restricted",
                        source_path=source,
                        cost=0,
                        deck_class=None,
                        deck_mode=None,
                        card_dbf_ids=[],
                    )
                    artifact = Path(stored["artifact_path"])
                    self.assertEqual(cache_root.stat().st_mode & 0o777, 0o755)
                    self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o755)
                    self.assertEqual(artifact.stat().st_mode & 0o777, 0o644)

                    # A hot-cache hit also repairs entries left by an older worker.
                    artifact.parent.chmod(0o750)
                    artifact.chmod(0o640)
                    self.assertIsNotNone(lookup_render_cache("restricted-code", "Restricted"))
                    self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o755)
                    self.assertEqual(artifact.stat().st_mode & 0o777, 0o644)
            finally:
                os.umask(previous_umask)

    def test_scoped_read_flag_overrides_global_flag(self):
        with patch.dict(
            os.environ,
            {
                "DECKVIEW_RENDER_CACHE_READ": "0",
                "DECKVIEW_RENDER_CACHE_READ_API": "1",
                "DECKVIEW_RENDER_CACHE_READ_TELEGRAM": "0",
            },
        ):
            from deckview.infrastructure.render_cache import render_cache_read_enabled

            self.assertTrue(render_cache_read_enabled("api"))
            self.assertFalse(render_cache_read_enabled("telegram"))

    def test_preview_derivative_is_small_atomic_and_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "render-cache"
            source = root / "source.jpg"
            Image.new("RGB", (2048, 2048), (31, 52, 73)).save(
                source,
                "JPEG",
                quality=95,
            )
            environment = {
                "WEB_DATABASE_PATH": str(root / "cache.db"),
                "DECKVIEW_RENDER_CACHE_ROOT": str(cache_root),
                "DECKVIEW_RENDER_CACHE_WRITE": "1",
                "DECKVIEW_RENDER_CACHE_READ": "1",
            }
            with patch.dict(os.environ, environment):
                stored = store_render_cache(
                    deck_code="preview-code",
                    deck_name="Preview",
                    source_path=source,
                    cost=0,
                    deck_class=None,
                    deck_mode=None,
                    card_dbf_ids=[],
                    image_style="parchment",
                    generate_preview=True,
                )

                self.assertIsNotNone(stored)
                preview_path = Path(stored["preview_artifact_path"])
                self.assertTrue(preview_path.is_file())
                self.assertEqual(preview_path.suffix, ".webp")
                self.assertLess(preview_path.stat().st_size, source.stat().st_size)
                with Image.open(preview_path) as preview:
                    self.assertLessEqual(max(preview.size), 720)

                with patch("deckview.infrastructure.render_cache.Image.open") as image_open:
                    reused = attach_render_preview(stored)
                image_open.assert_not_called()
                self.assertEqual(
                    reused["preview_filename"],
                    stored["preview_filename"],
                )

                preview_path.write_bytes(b"corrupt-preview")
                repaired = attach_render_preview(stored)
                self.assertEqual(repaired["preview_filename"], stored["preview_filename"])
                with Image.open(preview_path) as preview:
                    self.assertEqual(preview.format, "WEBP")

    def test_telegram_store_does_not_prepare_web_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            Image.new("RGB", (1024, 768), (50, 60, 70)).save(source, "JPEG")
            environment = {
                "WEB_DATABASE_PATH": str(root / "cache.db"),
                "DECKVIEW_RENDER_CACHE_ROOT": str(root / "render-cache"),
                "DECKVIEW_RENDER_CACHE_WRITE": "1",
            }
            with patch.dict(os.environ, environment):
                with patch("deckview.infrastructure.render_cache.Image.open") as image_open:
                    stored = store_render_cache(
                        deck_code="telegram-code",
                        deck_name="Telegram",
                        source_path=source,
                        cost=0,
                        deck_class=None,
                        deck_mode=None,
                        card_dbf_ids=[],
                    )

            self.assertIsNotNone(stored)
            self.assertNotIn("preview_filename", stored)
            image_open.assert_not_called()

    def test_concurrent_preview_writers_publish_one_valid_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "render-cache"
            source = cache_root / "aa" / f"{'a' * 64}.jpg"
            source.parent.mkdir(parents=True)
            Image.new("RGB", (1024, 768), (90, 20, 40)).save(source, "JPEG")
            entry = {
                "cache_key": "a" * 64,
                "filename": f"render-cache/aa/{'a' * 64}.jpg",
                "artifact_path": str(source),
            }
            with patch.dict(
                os.environ,
                {"DECKVIEW_RENDER_CACHE_ROOT": str(cache_root)},
            ):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    results = list(executor.map(lambda _: attach_render_preview(entry), range(4)))

            filenames = {result["preview_filename"] for result in results}
            self.assertEqual(len(filenames), 1)
            preview_path = Path(results[0]["preview_artifact_path"])
            with Image.open(preview_path) as preview:
                self.assertEqual(preview.format, "WEBP")


if __name__ == "__main__":
    unittest.main()
