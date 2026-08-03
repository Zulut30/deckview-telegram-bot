import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckview.repositories import web as web_db
from image_creator.personalization import (
    classify_deck_layout,
    normalize_cards_per_row,
    normalize_mana_curve_mode,
    resolve_cards_per_row,
)
from image_creator.text_size import normalize_title_size, title_size_scale


class UserImageStyleTest(unittest.TestCase):
    def test_deck_layout_options_are_validated_and_classified(self):
        self.assertEqual(normalize_cards_per_row(5), 5)
        self.assertEqual(normalize_cards_per_row(11), 0)
        self.assertEqual(normalize_cards_per_row(-1, allow_inherit=True), -1)
        self.assertEqual(normalize_mana_curve_mode("image"), "image")
        self.assertEqual(normalize_mana_curve_mode("bad"), "chart")
        normal = {"a": 2, **{f"n{i}": 1 for i in range(28)}}
        highlander = {f"h{i}": 1 for i in range(30)}
        extended = {"a": 2, **{f"e{i}": 1 for i in range(39)}}
        self.assertEqual(classify_deck_layout(normal), "normal")
        self.assertEqual(classify_deck_layout(highlander), "highlander")
        self.assertEqual(classify_deck_layout(extended), "extended")
        self.assertEqual(
            classify_deck_layout({**highlander, "module": 2}, {"module"}),
            "highlander",
        )
        self.assertEqual(resolve_cards_per_row({"normal": 5}, "normal", 22), 5)
        self.assertEqual(resolve_cards_per_row(None, "normal", 22), 8)
        self.assertEqual(resolve_cards_per_row(None, "highlander", 30), 8)

    def test_title_size_presets_are_ordered_and_validated(self):
        self.assertLess(title_size_scale("small"), title_size_scale("normal"))
        self.assertLess(title_size_scale("normal"), title_size_scale("large"))
        self.assertLess(title_size_scale("large"), title_size_scale("xlarge"))
        self.assertLess(title_size_scale("xlarge"), title_size_scale("huge"))
        self.assertGreaterEqual(title_size_scale("xlarge"), 1.7)
        self.assertEqual(normalize_title_size("unknown"), "normal")

    def test_style_is_persisted_and_invalid_values_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "deckview.sqlite3"
            with patch.object(web_db, "WEB_DATABASE_PATH", str(database)):
                previous_initialized = web_db._db_initialized
                web_db._db_initialized = False
                try:
                    web_db.init_db()
                    self.assertEqual(web_db.get_user_image_style(42), "classic")
                    self.assertEqual(
                        web_db.set_user_image_style(42, "parchment"),
                        "parchment",
                    )
                    self.assertEqual(web_db.get_user_image_style(42), "parchment")
                    self.assertEqual(
                        web_db.set_user_image_style(42, "unknown"),
                        "classic",
                    )
                    self.assertEqual(web_db.get_user_image_style(42), "classic")
                    custom = web_db.set_user_custom_background(
                        42,
                        "gradient",
                        "#112233,#AABBCC",
                    )
                    self.assertEqual(custom["style"], "custom")
                    self.assertEqual(custom["background_kind"], "gradient")
                    self.assertEqual(custom["revision"], 1)
                    self.assertEqual(
                        web_db.set_user_image_font(42, "oswald"),
                        "oswald",
                    )
                    self.assertEqual(
                        web_db.get_user_image_settings(42)["font"],
                        "oswald",
                    )
                    self.assertEqual(
                        web_db.set_user_image_text_size(42, "large"),
                        "large",
                    )
                    self.assertEqual(
                        web_db.get_user_image_settings(42)["text_size"],
                        "large",
                    )
                    self.assertEqual(
                        web_db.set_user_background_blur(42, 50),
                        50,
                    )
                    self.assertEqual(
                        web_db.get_user_image_settings(42)["blur"],
                        50,
                    )
                    self.assertEqual(
                        web_db.normalize_background_blur(0.25),
                        25,
                    )
                    before_preferences = web_db.get_user_image_settings(42)[
                        "personalization_revision"
                    ]
                    self.assertEqual(
                        web_db.set_user_dust_display(42, "hidden"),
                        "hidden",
                    )
                    with self.assertRaisesRegex(ValueError, "not uploaded"):
                        web_db.set_user_class_art_mode(42, "logo")
                    logo_settings = web_db.set_user_custom_logo(
                        42,
                        "user_assets/logos/test.png",
                    )
                    self.assertEqual(logo_settings["class_art_mode"], "logo")
                    self.assertEqual(
                        web_db.set_user_class_art_mode(42, "class"),
                        "class",
                    )
                    updated_preferences = web_db.get_user_image_settings(42)
                    self.assertEqual(
                        updated_preferences["dust_display"],
                        "hidden",
                    )
                    self.assertGreater(
                        updated_preferences["personalization_revision"],
                        before_preferences,
                    )
                    self.assertEqual(
                        web_db.set_user_cards_per_row(42, "normal", 5),
                        5,
                    )
                    with self.assertRaisesRegex(ValueError, "not uploaded"):
                        web_db.set_user_mana_curve_mode(42, "image")
                    curve_settings = web_db.set_user_mana_curve_image(
                        42,
                        "user_assets/mana_curve/user.png",
                    )
                    self.assertEqual(curve_settings["mana_curve_mode"], "image")
                    saved = web_db.save_user_image_design(
                        42,
                        "  Тёмная   классика  ",
                    )
                    self.assertEqual(saved["name"], "Тёмная классика")
                    self.assertEqual(
                        len(web_db.get_user_image_designs(42)),
                        1,
                    )
                    web_db.set_user_image_style(42, "classic")
                    web_db.set_user_dust_display(42, "normal")
                    web_db.set_user_image_font(42, "auto")
                    applied = web_db.apply_user_image_design(
                        42,
                        saved["id"],
                    )
                    self.assertEqual(applied["style"], "custom")
                    self.assertEqual(applied["font"], "oswald")
                    self.assertEqual(applied["dust_display"], "hidden")
                    self.assertEqual(applied["cards_per_row_normal"], 5)
                    self.assertEqual(applied["mana_curve_mode"], "image")
                    self.assertIsNone(
                        web_db.apply_user_image_design(99, saved["id"])
                    )
                    web_db.save_user_image_design(
                        42,
                        "тёмная классика",
                    )
                    self.assertEqual(
                        len(web_db.get_user_image_designs(42)),
                        1,
                    )
                    self.assertTrue(
                        web_db.delete_user_image_design(42, saved["id"])
                    )
                    self.assertFalse(
                        web_db.delete_user_image_design(42, saved["id"])
                    )

                    web_db.register_managed_chat(
                        -10042,
                        "Test channel",
                        "channel",
                        42,
                    )
                    web_db.register_managed_chat(
                        -10042,
                        "Test channel",
                        "channel",
                        99,
                    )
                    self.assertEqual(
                        [chat["chat_id"] for chat in web_db.get_managed_chats_for_user(42)],
                        [-10042],
                    )
                    self.assertEqual(
                        [chat["chat_id"] for chat in web_db.get_managed_chats_for_user(99)],
                        [-10042],
                    )
                    web_db.set_managed_chat_disabled_commands(
                        -10042,
                        ["meta", "card"],
                    )
                    managed = web_db.get_managed_chat(-10042)
                    self.assertEqual(managed["disabled_commands"], ["card", "meta"])
                    self.assertFalse(
                        web_db.is_managed_chat_command_enabled(-10042, "meta")
                    )
                    self.assertTrue(
                        web_db.is_managed_chat_command_enabled(-10042, "arena")
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_background_blur(-10042, 1),
                        100,
                    )
                    self.assertEqual(
                        web_db.get_managed_chat(-10042)[
                            "custom_background_blur"
                        ],
                        100,
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_image_text_size(
                            -10042,
                            "xlarge",
                        ),
                        "xlarge",
                    )
                    self.assertEqual(
                        web_db.get_managed_chat(-10042)["image_text_size"],
                        "xlarge",
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_image_font(
                            -10042,
                            "merriweather",
                        ),
                        "merriweather",
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_dust_display(
                            -10042,
                            "large",
                        ),
                        "large",
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_cards_per_row(
                            -10042,
                            "highlander",
                            10,
                        ),
                        10,
                    )
                    web_db.set_managed_chat_mana_curve_image(
                        -10042,
                        "user_assets/mana_curve/chat.png",
                    )
                    self.assertEqual(
                        web_db.set_managed_chat_deck_button_layout(
                            -10042,
                            "compact",
                        ),
                        "compact",
                    )
                    with self.assertRaisesRegex(ValueError, "not uploaded"):
                        web_db.set_managed_chat_class_art_mode(
                            -10042,
                            "logo",
                        )
                    web_db.set_managed_chat_custom_logo(
                        -10042,
                        "user_assets/logos/chat.png",
                    )
                    managed = web_db.get_managed_chat(-10042)
                    self.assertEqual(managed["class_art_mode"], "logo")
                    self.assertEqual(managed["image_font"], "merriweather")
                    self.assertEqual(managed["image_dust_display"], "large")
                    self.assertEqual(managed["cards_per_row_highlander"], 10)
                    self.assertEqual(managed["mana_curve_mode"], "image")
                    self.assertEqual(managed["deck_button_layout"], "compact")
                    chat_design = web_db.save_managed_chat_image_design(
                        42,
                        -10042,
                        "Дизайн группы",
                    )
                    web_db.set_managed_chat_image_font(-10042, "inherit")
                    web_db.set_managed_chat_dust_display(-10042, "inherit")
                    applied_chat = web_db.apply_user_image_design_to_chat(
                        42,
                        chat_design["id"],
                        -10042,
                    )
                    self.assertEqual(
                        applied_chat["image_font"],
                        "merriweather",
                    )
                    self.assertEqual(
                        applied_chat["image_dust_display"],
                        "large",
                    )
                    self.assertEqual(applied_chat["cards_per_row_highlander"], 10)
                    self.assertEqual(applied_chat["mana_curve_mode"], "image")
                    self.assertIsNone(
                        web_db.apply_user_image_design_to_chat(
                            99,
                            chat_design["id"],
                            -10042,
                        )
                    )
                finally:
                    web_db._db_initialized = previous_initialized


if __name__ == "__main__":
    unittest.main()
