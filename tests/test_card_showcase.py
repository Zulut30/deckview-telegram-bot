from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from image_creator.background_preview import build_gradient_preview
from image_creator.card_showcase import (
    BELWE_FONT_PATH,
    CANVAS_SIZE,
    PARCHMENT_PATH,
    WOOD_FRAME_PATH,
    _clear_showcase_layer_cache,
    build_card_showcase,
    build_full_art_showcase,
    parchment_background,
    wood_frame_overlay,
)
from image_creator.custom_background import GRADIENT_PRESETS
from image_creator.font_catalog import FONT_OPTIONS, load_title_font
from image_creator.font_preview import build_font_preview


class CardShowcaseTests(unittest.TestCase):
    def setUp(self):
        _clear_showcase_layer_cache()

    def _card_bytes(self) -> bytes:
        image = Image.new("RGBA", (512, 776), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((90, 50, 422, 720), radius=45, fill=(65, 100, 150, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_required_local_assets_exist(self):
        self.assertTrue(PARCHMENT_PATH.is_file())
        self.assertTrue(WOOD_FRAME_PATH.is_file())
        self.assertTrue(BELWE_FONT_PATH.is_file())

    def test_decorative_layers_are_cached_but_callers_receive_safe_copies(self):
        parchment_first = parchment_background((160, 120))
        parchment_second = parchment_background((160, 120))
        frame_first = wood_frame_overlay((160, 120), 20)
        frame_second = wood_frame_overlay((160, 120), 20)

        self.assertIsNot(parchment_first, parchment_second)
        self.assertIsNot(frame_first, frame_second)

        parchment_original = parchment_second.getpixel((80, 60))
        frame_original = frame_second.getpixel((2, 2))
        parchment_first.putpixel((80, 60), (1, 2, 3, 4))
        frame_first.putpixel((2, 2), (5, 6, 7, 8))

        self.assertEqual(parchment_second.getpixel((80, 60)), parchment_original)
        self.assertEqual(frame_second.getpixel((2, 2)), frame_original)

    def test_showcase_is_telegram_ready_jpeg(self):
        result = build_card_showcase(self._card_bytes(), "Картонный голем")
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, CANVAS_SIZE)
            self.assertEqual(image.width, image.height)
            self.assertEqual(image.mode, "RGB")

    def test_long_cyrillic_name_renders(self):
        result = build_card_showcase(
            self._card_bytes(),
            "Очень длинное название легендарной карты на русском языке",
        )
        self.assertGreater(len(result), 20_000)

    def test_full_art_showcase_is_square_telegram_jpeg(self):
        art = Image.new("RGB", (1448, 2048), (40, 90, 130))
        buffer = BytesIO()
        art.save(buffer, format="WEBP")
        result = build_full_art_showcase(buffer.getvalue(), "Картонный голем")
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, CANVAS_SIZE)
            self.assertEqual(image.width, image.height)
            self.assertEqual(image.mode, "RGB")

    def test_font_preview_contains_all_available_cyrillic_fonts(self):
        for key in FONT_OPTIONS:
            font = load_title_font(key, 36)
            self.assertGreater(font.getlength("ЛЕГЕНДА"), 0)
        result = build_font_preview("oswald")
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (900, 1660))

    def test_gradient_preview_contains_every_preset(self):
        self.assertGreaterEqual(len(GRADIENT_PRESETS), 10)
        result = build_gradient_preview("#10274A,#238BC5")
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (900, 1280))


if __name__ == "__main__":
    unittest.main()
