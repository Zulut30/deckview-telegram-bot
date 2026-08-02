from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageStat

from image_creator import custom_background
from image_creator.cards_placer import (
    _fit_asset_to_slot,
    _make_custom_mana_curve_overlay,
    _paste_card_in_cell,
)
from image_creator.font_catalog import load_title_font


class CustomBackgroundTests(unittest.TestCase):
    def setUp(self):
        custom_background._clear_background_cache()

    def test_blur_accepts_fractional_and_percentage_levels(self):
        self.assertEqual(custom_background.normalize_background_blur(0), 0)
        self.assertEqual(custom_background.normalize_background_blur(0.25), 25)
        self.assertEqual(custom_background.normalize_background_blur("0.5"), 50)
        self.assertEqual(custom_background.normalize_background_blur(1), 100)
        self.assertEqual(custom_background.normalize_background_blur(100), 100)

    def test_uploaded_background_blurs_without_changing_canvas_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "checker.png"
            source = Image.new("RGB", (400, 400), "black")
            draw = ImageDraw.Draw(source)
            for y in range(0, 400, 40):
                for x in range(0, 400, 40):
                    if (x // 40 + y // 40) % 2:
                        draw.rectangle((x, y, x + 39, y + 39), fill="white")
            source.save(path)

            with patch.object(custom_background, "_BACKGROUND_ROOT", root):
                sharp = custom_background.decorative_background(
                    (400, 400),
                    {"kind": "image", "value": str(path), "blur": 0},
                ).convert("RGB")
                blurred = custom_background.decorative_background(
                    (400, 400),
                    {"kind": "image", "value": str(path), "blur": 100},
                ).convert("RGB")

            self.assertEqual(sharp.size, blurred.size)
            self.assertLess(
                sum(ImageStat.Stat(blurred).var),
                sum(ImageStat.Stat(sharp).var),
            )

    def test_uploaded_background_without_blur_preserves_source_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "dark.png"
            source_color = (12, 18, 27)
            Image.new("RGB", (320, 240), source_color).save(path)

            with patch.object(custom_background, "_BACKGROUND_ROOT", root):
                result = custom_background.decorative_background(
                    (640, 480),
                    {"kind": "image", "value": str(path), "blur": 0},
                ).convert("RGB")

            self.assertEqual(result.getpixel((320, 240)), source_color)

    def test_prepared_background_is_cached_and_returned_as_a_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "background.png"
            Image.new("RGB", (320, 240), "navy").save(path)

            with (
                patch.object(custom_background, "_BACKGROUND_ROOT", root),
                patch.object(
                    custom_background.ImageOps,
                    "fit",
                    wraps=custom_background.ImageOps.fit,
                ) as fit,
            ):
                first = custom_background.decorative_background(
                    (400, 400),
                    {"kind": "image", "value": str(path), "blur": 25},
                )
                first.putpixel((0, 0), (255, 0, 0, 255))
                second = custom_background.decorative_background(
                    (400, 400),
                    {"kind": "image", "value": str(path), "blur": 25},
                )

            self.assertEqual(fit.call_count, 1)
            self.assertNotEqual(second.getpixel((0, 0)), (255, 0, 0, 255))

    def test_custom_mana_curve_is_transparent_and_high_contrast(self):
        label_font = load_title_font("hearthstone", 36)
        count_font = load_title_font("belwe", 30)
        overlay, offset = _make_custom_mana_curve_overlay(
            976,
            318,
            {0: 0, 1: 2, 2: 6, 3: 2, 4: 7, 5: 5, 6: 2, 7: 6},
            label_font,
            count_font,
        )

        self.assertEqual(overlay.mode, "RGBA")
        self.assertEqual(offset, (-24, -44))
        self.assertEqual(overlay.getpixel((0, 0))[3], 0)
        visible = [
            pixel
            for pixel in overlay.get_flattened_data()
            if pixel[3] >= 200
        ]
        self.assertTrue(any(min(pixel[:3]) >= 235 for pixel in visible))
        self.assertTrue(
            any(
                pixel[2] > pixel[0] + 45 and pixel[2] > pixel[1] + 20
                for pixel in visible
            )
        )

    def test_cards_keep_aspect_on_top_baseline_and_assets_center_in_slots(self):
        tall = Image.new("RGBA", (200, 500), (255, 255, 255, 255))
        wide = Image.new("RGBA", (300, 500), (255, 255, 255, 255))
        badge = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
        _, tall_layout = _paste_card_in_cell(tall, 550, 677, badge, True)
        _, wide_layout = _paste_card_in_cell(wide, 550, 677, badge, True)
        self.assertEqual(tall_layout[1], 0)
        self.assertEqual(wide_layout[1], 0)
        self.assertEqual(tall_layout[3], 677)
        self.assertEqual(wide_layout[3], 677)
        self.assertAlmostEqual(tall_layout[2] / 677, 200 / 500, delta=0.01)
        self.assertAlmostEqual(wide_layout[2] / 677, 300 / 500, delta=0.01)

        asset = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
        fitted, position = _fit_asset_to_slot(asset, (100, 200, 400, 400))
        self.assertEqual(position[0] + fitted.width // 2, 300)
        self.assertEqual(position[1] + fitted.height // 2, 400)


if __name__ == "__main__":
    unittest.main()
