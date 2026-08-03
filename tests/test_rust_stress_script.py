"""Fast unit tests for the bulk native-render verification script."""

from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from scripts.stress_test_rust_renderer import (
    SCENARIOS,
    load_deck_cases,
    percentile,
    scenario_settings,
    selected_scenarios,
)


class RustStressScriptTests(unittest.TestCase):
    def test_percentile_is_deterministic_at_boundaries(self):
        self.assertEqual(percentile([], 0.95), 0.0)
        self.assertEqual(percentile([30, 10, 20], 0.0), 10.0)
        self.assertEqual(percentile([30, 10, 20], 1.0), 30.0)

    def test_all_scenarios_cover_supported_styles_and_hidden_footer(self):
        names = selected_scenarios("all")
        self.assertEqual(names, list(SCENARIOS))
        self.assertEqual(
            {SCENARIOS[name]["image_style"] for name in names},
            {"classic", "parchment", "custom"},
        )
        self.assertEqual(
            SCENARIOS["custom-minimal"]["image_mana_curve"]["mode"],
            "hidden",
        )

    def test_loads_json_and_plain_text_deck_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "decks.json"
            json_path.write_text(
                json.dumps(
                    {
                        "decks": [
                            {"title": "Reno Mage", "code": "AAAA"},
                            "BBBB",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            text_path = root / "decks.txt"
            text_path.write_text(
                "# comment\nControl Priest\tCCCC\nDDDD\n",
                encoding="utf-8",
            )

            json_cases = load_deck_cases(json_path)
            text_cases = load_deck_cases(text_path)

        self.assertEqual([case.code for case in json_cases], ["AAAA", "BBBB"])
        self.assertEqual(json_cases[0].slug, "reno-mage")
        self.assertEqual([case.code for case in text_cases], ["CCCC", "DDDD"])

    def test_rejects_unknown_scenario(self):
        with self.assertRaisesRegex(ValueError, "unknown scenarios"):
            selected_scenarios("classic,unknown")

    def test_optional_assets_override_custom_scenarios(self):
        args = Namespace(
            custom_background=Path("/tmp/background.png"),
            background_blur=50,
            custom_logo=Path("/tmp/logo.png"),
            mana_image=Path("/tmp/mana.png"),
        )
        gradient = scenario_settings("custom-gradient", args)
        minimal = scenario_settings("custom-minimal", args)

        self.assertEqual(gradient["image_background"]["kind"], "image")
        self.assertEqual(gradient["image_background"]["blur"], 50)
        self.assertEqual(gradient["image_class_art"]["mode"], "logo")
        self.assertEqual(minimal["image_mana_curve"]["mode"], "image")


if __name__ == "__main__":
    unittest.main()
