"""Regression tests for opt-in image-rendering optimizations."""

import os
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from image_creator.cards_placer import (
    _clear_card_cell_cache,
    _grid_cell_size,
    _is_sideboard_card_id,
    _paste_card_in_cell,
    _prepared_card_cell,
    _tint_sideboard,
)
from image_creator.prepared_card_cache import (
    _prune_prepared_card_shard,
    load_prepared_card,
    store_prepared_card,
)


class FastPillowTests(unittest.TestCase):
    def setUp(self):
        _clear_card_cell_cache()

    def test_sideboard_fast_path_is_bit_exact(self):
        rng = np.random.default_rng(20260716)
        source = Image.fromarray(
            rng.integers(0, 256, size=(73, 59, 4), dtype=np.uint8),
            mode="RGBA",
        )

        with patch.dict(os.environ, {"DECKVIEW_FAST_PIL": "0"}):
            legacy = np.asarray(_tint_sideboard(source))
        with patch.dict(os.environ, {"DECKVIEW_FAST_PIL": "1"}):
            optimized = np.asarray(_tint_sideboard(source))

        self.assertTrue(np.array_equal(legacy, optimized))

    def test_grid_cell_is_wide_enough_for_wide_card_frames(self):
        self.assertEqual(_grid_cell_size(500), (550, 677))

    def test_location_frame_gets_only_a_small_optical_width_correction(self):
        badge = Image.new("RGBA", (1, 1), "white")
        narrow = Image.new("RGBA", (95, 140), "blue")
        naturally_wide = Image.new("RGBA", (110, 140), "green")

        _, plain_layout = _paste_card_in_cell(
            narrow, 120, 140, badge, True
        )
        _, location_layout = _paste_card_in_cell(
            narrow, 120, 140, badge, True, card_type="LOCATION"
        )
        _, wide_layout = _paste_card_in_cell(
            naturally_wide, 120, 140, badge, True, card_type="SPELL"
        )

        self.assertEqual(plain_layout[2], 95)
        self.assertEqual(location_layout[2], 99)
        self.assertEqual(location_layout[3], 140)
        self.assertEqual(wide_layout[2], 110)

    def test_sideboard_detection_requires_explicit_suffix(self):
        self.assertTrue(_is_sideboard_card_id("123-real-side"))
        self.assertFalse(_is_sideboard_card_id("76318-beaming-sidekick"))
        self.assertFalse(_is_sideboard_card_id("456-lakeside-ambusher"))
        self.assertFalse(_is_sideboard_card_id("123-real-side", set()))
        self.assertTrue(
            _is_sideboard_card_id("123-real-side", {"123-real-side"})
        )

    def test_sideboard_fast_path_preserves_alpha(self):
        pixels = np.array(
            [
                [[0, 0, 0, 0], [200, 230, 240, 17]],
                [[255, 255, 255, 128], [1, 2, 3, 255]],
            ],
            dtype=np.uint8,
        )
        source = Image.fromarray(pixels, mode="RGBA")

        with patch.dict(os.environ, {"DECKVIEW_FAST_PIL": "1"}):
            result = np.asarray(_tint_sideboard(source))

        self.assertTrue(np.array_equal(result[:, :, 3], pixels[:, :, 3]))

    def test_cards_with_different_aspect_ratios_share_top_baseline(self):
        water = Image.new("RGBA", (20, 10), "white")
        tall = Image.new("RGBA", (100, 140), "blue")
        short = Image.new("RGBA", (100, 100), "red")

        tall_cell, tall_layout = _paste_card_in_cell(
            tall, 100, 140, water, True
        )
        short_cell, short_layout = _paste_card_in_cell(
            short, 100, 140, water, True
        )

        self.assertEqual(tall_cell.getchannel("A").getbbox()[1], 0)
        self.assertEqual(short_cell.getchannel("A").getbbox()[1], 0)
        self.assertEqual(tall_layout[1], 0)
        self.assertEqual(short_layout[1], 0)

    def test_different_card_frames_keep_aspect_ratio_in_centered_grid_cells(self):
        water = Image.new("RGBA", (20, 10), "white")
        sources = [
            Image.new("RGBA", (90, 140), "blue"),
            Image.new("RGBA", (100, 140), "red"),
            Image.new("RGBA", (110, 140), "green"),
        ]

        cells_and_layouts = [
            _paste_card_in_cell(source, 120, 140, water, True)
            for source in sources
        ]
        layouts = [layout for _cell, layout in cells_and_layouts]

        for source, layout in zip(sources, layouts):
            ox, _oy, width, height = layout
            self.assertAlmostEqual(
                width / height,
                source.width / source.height,
                delta=0.01,
            )
            self.assertLessEqual(abs((ox * 2 + width) - 120), 1)

    def test_faint_export_shadow_does_not_shrink_visible_card_frame(self):
        water = Image.new("RGBA", (20, 10), "white")
        source = Image.new("RGBA", (109, 180), (0, 0, 0, 0))
        source.paste((80, 120, 180, 255), (0, 0, 109, 140))
        # A sparse opaque export artefact is enough to expand a plain alpha
        # bounding box, but must not count as part of the visible card frame.
        source.paste((0, 0, 0, 255), (0, 140, 2, 180))

        cell, layout = _paste_card_in_cell(
            source, 100, 140, water, True
        )
        visible = cell.getchannel("A").point(
            [0] * 128 + [255] * 128
        ).getbbox()

        self.assertEqual(layout[1], 0)
        self.assertEqual(layout[3], 140)
        self.assertEqual(visible[1], 0)
        self.assertEqual(visible[3], 140)

    def test_prepared_card_cell_reuses_decoded_image(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.png"
            source = Image.new("RGBA", (120, 180), (0, 0, 0, 0))
            source.paste((20, 40, 80, 255), (10, 12, 110, 172))
            source.save(path)
            water = Image.new("RGBA", (20, 10), "white")

            with patch("image_creator.cards_placer.Image.open", wraps=Image.open) as opened:
                first = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )
                second = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )

        self.assertIsNotNone(first)
        self.assertIs(first, second)
        self.assertEqual(opened.call_count, 1)

    def test_prepared_card_cell_reuses_shared_disk_cache_after_memory_clear(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "card.webp"
            cache_root = root / "prepared-cards"
            source = Image.new("RGBA", (120, 180), (0, 0, 0, 0))
            source.paste((20, 40, 80, 255), (10, 12, 110, 172))
            source.save(path, "WEBP", lossless=True)
            water = Image.new("RGBA", (20, 10), "white")

            with (
                patch.dict(
                    os.environ,
                    {
                        "DECKVIEW_PREPARED_CARD_CACHE": "1",
                        "DECKVIEW_PREPARED_CARD_CACHE_ROOT": str(cache_root),
                    },
                ),
                patch(
                    "image_creator.cards_placer.Image.open",
                    wraps=Image.open,
                ) as opened,
            ):
                first = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )
                _clear_card_cell_cache()
                second = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertTrue(np.array_equal(first[0], second[0]))
            self.assertEqual(first[1], second[1])
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(len(list(cache_root.rglob("*.rgba"))), 1)

    def test_prepared_cache_accepts_signed_layout_offsets(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_root = root / "prepared-cards"
            cell = Image.new("RGBA", (100, 140), "blue")
            key = ("signed-layout", 1)
            expected = (cell, (-5, 0, 110, 140))

            with patch.dict(
                os.environ,
                {
                    "DECKVIEW_PREPARED_CARD_CACHE": "1",
                    "DECKVIEW_PREPARED_CARD_CACHE_ROOT": str(cache_root),
                },
            ):
                store_prepared_card(key, expected)
                loaded = load_prepared_card(key, (100, 140))

            self.assertIsNotNone(loaded)
            self.assertEqual(expected[1], loaded[1])
            self.assertTrue(np.array_equal(expected[0], loaded[0]))

    def test_prepared_card_disk_cache_changes_with_source_revision(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "card.webp"
            cache_root = root / "prepared-cards"
            water = Image.new("RGBA", (20, 10), "white")
            environment = {
                "DECKVIEW_PREPARED_CARD_CACHE": "1",
                "DECKVIEW_PREPARED_CARD_CACHE_ROOT": str(cache_root),
            }

            Image.new("RGBA", (120, 180), "blue").save(
                path,
                "WEBP",
                lossless=True,
            )
            with patch.dict(os.environ, environment):
                first = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )
                old_mtime = path.stat().st_mtime_ns
                Image.new("RGBA", (120, 180), "red").save(
                    path,
                    "WEBP",
                    lossless=True,
                )
                os.utime(path, ns=(old_mtime + 1_000_000, old_mtime + 1_000_000))
                _clear_card_cell_cache()
                second = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )

            self.assertFalse(np.array_equal(first[0], second[0]))
            self.assertEqual(len(list(cache_root.rglob("*.rgba"))), 2)

    def test_corrupt_prepared_card_disk_cache_falls_back_to_source(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "card.webp"
            cache_root = root / "prepared-cards"
            Image.new("RGBA", (120, 180), "blue").save(
                path,
                "WEBP",
                lossless=True,
            )
            water = Image.new("RGBA", (20, 10), "white")
            environment = {
                "DECKVIEW_PREPARED_CARD_CACHE": "1",
                "DECKVIEW_PREPARED_CARD_CACHE_ROOT": str(cache_root),
            }

            with patch.dict(os.environ, environment):
                expected = _prepared_card_cell(
                    str(path),
                    (100, 140),
                    is_sideboard=False,
                    water=water,
                )
                cache_file = next(cache_root.rglob("*.rgba"))
                cache_file.write_bytes(b"corrupt")
                _clear_card_cell_cache()
                with patch(
                    "image_creator.cards_placer.Image.open",
                    wraps=Image.open,
                ) as opened:
                    recovered = _prepared_card_cell(
                        str(path),
                        (100, 140),
                        is_sideboard=False,
                        water=water,
                    )

            self.assertEqual(opened.call_count, 1)
            self.assertTrue(np.array_equal(expected[0], recovered[0]))
            self.assertGreater(cache_file.stat().st_size, len(b"corrupt"))

    def test_prepared_card_cache_prunes_oldest_files_within_shard(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            shard = Path(directory)
            oldest = shard / "oldest.rgba"
            middle = shard / "middle.rgba"
            newest = shard / "newest.rgba"
            oldest.write_bytes(b"a" * 10)
            middle.write_bytes(b"b" * 10)
            newest.write_bytes(b"c" * 10)
            os.utime(oldest, ns=(1, 1))
            os.utime(middle, ns=(2, 2))
            os.utime(newest, ns=(3, 3))

            _prune_prepared_card_shard(shard, max_bytes=20)

            self.assertFalse(oldest.exists())
            self.assertTrue(middle.exists())
            self.assertTrue(newest.exists())


if __name__ == "__main__":
    unittest.main()
