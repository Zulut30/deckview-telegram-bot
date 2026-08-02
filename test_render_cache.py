"""Tests for the fail-open, versioned 21-day render cache."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from render_cache import (
    build_render_cache_key,
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
            source.write_bytes(b"jpeg-test-bytes")
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
                Path(stored["artifact_path"]).unlink()
                missing = lookup_render_cache("code", "Name")

            self.assertIsNotNone(stored)
            self.assertEqual(hit["cost"], 100)
            self.assertEqual(hit["card_dbf_ids"], [1, 2])
            self.assertEqual(hit["cache_layer"], "memory")
            self.assertEqual(copied, str(materialized))
            self.assertEqual(materialized.read_bytes(), source.read_bytes())
            self.assertIsNone(missing)
            with sqlite3.connect(database) as conn:
                ttl = conn.execute(
                    "SELECT (julianday(expires_at) - julianday(created_at)) * 24 FROM render_cache"
                ).fetchone()[0]
            self.assertAlmostEqual(ttl, 504, places=3)

    def test_disabled_write_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            source.write_bytes(b"jpeg-test-bytes")
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

    def test_scoped_read_flag_overrides_global_flag(self):
        with patch.dict(
            os.environ,
            {
                "DECKVIEW_RENDER_CACHE_READ": "0",
                "DECKVIEW_RENDER_CACHE_READ_API": "1",
                "DECKVIEW_RENDER_CACHE_READ_TELEGRAM": "0",
            },
        ):
            from render_cache import render_cache_read_enabled

            self.assertTrue(render_cache_read_enabled("api"))
            self.assertFalse(render_cache_read_enabled("telegram"))


if __name__ == "__main__":
    unittest.main()
