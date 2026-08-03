"""Contracts for the opt-in native renderer adapter."""

from __future__ import annotations

import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from image_creator.cards_placer import _build_rust_payload, _place_cards_rust


def jpeg_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), "navy").save(output, "JPEG")
    return output.getvalue()


class NativeRendererAdapterTests(unittest.TestCase):
    def test_dust_asset_is_a_real_transparent_png_for_both_renderers(self):
        path = Path(__file__).resolve().parents[1] / "assets" / "dust.png"
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            rgba = image.convert("RGBA")
        self.assertEqual(rgba.getchannel("A").getextrema(), (0, 255))

    def test_payload_matches_python_grid_spacing(self):
        payload = _build_rust_payload(
            {"card-one": 1},
            {"card-one": 2},
            100,
            {"cards": [], "class": {"slug": "mage"}},
            deck_name="Test",
        )

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["renderer_version"], "deckview-native/0.3.0")
        self.assertEqual(payload["layout"]["row_gap"], 72)
        self.assertEqual(payload["layout"]["top_margin"], 250)
        self.assertEqual(payload["deck"]["name"], "Test")
        self.assertEqual(payload["output"]["max_output_side"], 1920)
        self.assertEqual(payload["background"]["style"], "classic")
        self.assertEqual(payload["typography"]["title_scale"], 1.0)
        self.assertEqual(payload["mana_curve"]["mode"], "chart")
        self.assertTrue(payload["assets"]["allowed_roots"])

    def test_payload_carries_every_personalization_group(self):
        payload = _build_rust_payload(
            {"card-one": 2},
            {"card-one": 4},
            1600,
            {
                "cards": [],
                "class": {"slug": "death-knight"},
                "runeSlots": {"blood": 2, "frost": 1},
            },
            deck_name="Custom",
            image_style="custom",
            image_background={
                "kind": "gradient",
                "value": "#102030,#405060",
                "blur": 50,
            },
            image_font="belwe",
            image_text_size="large",
            image_dust_display="large",
            image_class_art={"mode": "logo", "path": "assets/title.png"},
            image_mana_curve={"mode": "hidden", "path": None},
        )

        self.assertEqual(payload["background"]["style"], "custom")
        self.assertEqual(payload["background"]["kind"], "gradient")
        self.assertEqual(payload["background"]["value"], "#102030,#405060")
        self.assertEqual(payload["background"]["blur"], 50)
        self.assertEqual(payload["typography"]["font_key"], "belwe")
        self.assertGreater(payload["typography"]["title_scale"], 1.0)
        self.assertEqual(payload["dust"]["mode"], "large")
        self.assertEqual(payload["class_art"]["mode"], "logo")
        self.assertEqual(payload["mana_curve"]["mode"], "hidden")
        self.assertEqual(sum(rune["count"] for rune in payload["runes"]), 3)

    def test_required_mode_never_hides_native_failure(self):
        module = SimpleNamespace(
            render_deck_image=lambda _payload: (_ for _ in ()).throw(
                RuntimeError("required native failed")
            )
        )
        with (
            patch.dict(
                os.environ,
                {
                    "DECKVIEW_RUST_RENDER": "1",
                    "DECKVIEW_RUST_RENDER_STRICT": "0",
                    "DECKVIEW_RUST_REQUIRED": "1",
                },
            ),
            patch.dict(sys.modules, {"deckview_core": module}),
        ):
            with self.assertRaisesRegex(RuntimeError, "required native failed"):
                _place_cards_rust(
                    {"card-one": 1},
                    {"card-one": 2},
                    8,
                    100,
                    {"cards": [], "class": {"slug": "mage"}},
                )

    def test_native_result_is_marked_with_actual_backend(self):
        module = SimpleNamespace(render_deck_image=lambda _payload: jpeg_bytes())
        with (
            patch.dict(
                os.environ,
                {
                    "DECKVIEW_RUST_RENDER": "1",
                    "DECKVIEW_RUST_RENDER_STRICT": "1",
                },
            ),
            patch.dict(sys.modules, {"deckview_core": module}),
        ):
            image = _place_cards_rust(
                {"card-one": 1},
                {"card-one": 2},
                8,
                100,
                {"cards": [], "class": {"slug": "mage"}},
            )

        self.assertEqual(image.info["deckview_renderer"], "rust")

    def test_strict_mode_never_hides_native_failure(self):
        module = SimpleNamespace(
            render_deck_image=lambda _payload: (_ for _ in ()).throw(
                RuntimeError("native failed")
            )
        )
        with (
            patch.dict(
                os.environ,
                {
                    "DECKVIEW_RUST_RENDER": "1",
                    "DECKVIEW_RUST_RENDER_STRICT": "1",
                },
            ),
            patch.dict(sys.modules, {"deckview_core": module}),
        ):
            with self.assertRaisesRegex(RuntimeError, "native failed"):
                _place_cards_rust(
                    {"card-one": 1},
                    {"card-one": 2},
                    8,
                    100,
                    {"cards": [], "class": {"slug": "mage"}},
                )


if __name__ == "__main__":
    unittest.main()
