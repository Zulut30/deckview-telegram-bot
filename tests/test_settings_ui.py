import tempfile
import asyncio
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

import main


def _sample_chat(**overrides):
    chat = {
        "chat_id": -100123456789,
        "added_by": None,
        "title": "Тестовый чат",
        "chat_type": "supergroup",
        "is_active": 1,
        "image_style": "inherit",
        "custom_background_kind": None,
        "custom_background_value": None,
        "custom_background_blur": 0,
        "image_font": "inherit",
        "image_text_size": "inherit",
        "image_dust_display": "inherit",
        "class_art_mode": "inherit",
        "custom_logo_path": None,
        "personalization_revision": 0,
        "cards_per_row_normal": -1,
        "cards_per_row_extended": -1,
        "cards_per_row_highlander": -1,
        "mana_curve_mode": "inherit",
        "mana_curve_image_path": None,
        "deck_button_layout": "full",
        "disabled_commands": [],
    }
    chat.update(overrides)
    return chat


class SettingsKeyboardTests(unittest.TestCase):
    def setUp(self):
        main._PERSONALIZATION_PREVIEW_GENERATION.clear()

    def test_add_link_targets_group_chat(self):
        self.assertIn("startgroup=", main._ADD_TO_CHAT_URL)
        self.assertIn("admin=manage_chat", main._ADD_TO_CHAT_URL)
        self.assertNotIn("startchannel", main._ADD_TO_CHAT_URL)
        self.assertIn("start=settings", main._PRIVATE_SETTINGS_URL)

    def test_home_is_two_rows_of_two_buttons(self):
        keyboard = main._settings_home_keyboard()
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 2, 1])

    def test_design_actions_are_compact(self):
        keyboard = main._settings_design_keyboard(
            {
                "style": "classic",
                "background_kind": None,
                "background_value": None,
                "blur": 0,
            }
        )
        self.assertTrue(all(len(row) <= 2 for row in keyboard.inline_keyboard))
        self.assertEqual(len(keyboard.inline_keyboard), 8)
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        }
        self.assertIn("settings_dust", callbacks)
        self.assertIn("settings_class_art", callbacks)
        self.assertIn("settings_design_save", callbacks)
        self.assertIn("settings_designs", callbacks)
        self.assertNotIn("settings_rows_menu:user:0", callbacks)
        self.assertIn("settings_mana_curve:user:0", callbacks)
        self.assertIn("settings_buttons_chats", callbacks)

    def test_personal_design_links_directly_to_group_button_presets(self):
        chats = [
            _sample_chat(chat_id=-101, title="Первый"),
            _sample_chat(chat_id=-102, title="Второй"),
            _sample_chat(chat_id=-103, title="Третий"),
        ]
        with patch.object(main, "get_managed_chats_for_user", return_value=chats):
            keyboard = main._managed_chat_buttons_hub_keyboard(42)
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 1, 1])
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("settings_chat_buttons:-101", callbacks)
        self.assertEqual(callbacks[-1], "settings_design")

    def test_mana_curve_menu_is_compact_and_row_setting_is_removed(self):
        settings = {
            "mana_curve_mode": "hidden",
        }
        curve = main._mana_curve_keyboard("user", 42, settings)
        self.assertTrue(all(len(row) <= 2 for row in curve.inline_keyboard))
        self.assertTrue(any(
            button.text.startswith("✅ Скрыта")
            for row in curve.inline_keyboard for button in row
        ))

    def test_dust_and_class_art_controls_are_compact(self):
        dust = main._dust_display_keyboard("hidden")
        art = main._class_art_keyboard(
            {"class_art_mode": "logo", "custom_logo_path": "logo.png"}
        )
        self.assertTrue(all(len(row) <= 2 for row in dust.inline_keyboard))
        self.assertTrue(all(len(row) <= 2 for row in art.inline_keyboard))
        self.assertTrue(
            any(
                button.text.startswith("✅ Скрыта")
                for row in dust.inline_keyboard
                for button in row
            )
        )

    def test_saved_designs_have_apply_delete_and_confirmation(self):
        designs = [{"id": 7, "name": "Тёмная классика"}]
        keyboard = main._saved_designs_keyboard(designs)
        self.assertEqual(
            keyboard.inline_keyboard[0][0].callback_data,
            "settings_design_apply:7",
        )
        self.assertEqual(
            keyboard.inline_keyboard[0][1].callback_data,
            "settings_design_delete:7",
        )
        confirmation = main._saved_designs_keyboard(
            designs,
            confirm_delete_id=7,
        )
        self.assertEqual(
            confirmation.inline_keyboard[0][0].callback_data,
            "settings_design_delete_confirm:7",
        )

    def test_text_size_presets_are_compact_for_user_and_chat(self):
        user_keyboard = main._text_size_keyboard("user", 42, "large")
        chat_keyboard = main._text_size_keyboard("chat", -10042, "inherit")
        self.assertTrue(
            all(len(row) <= 2 for row in user_keyboard.inline_keyboard)
        )
        self.assertTrue(
            all(len(row) <= 2 for row in chat_keyboard.inline_keyboard)
        )
        user_callbacks = [
            button.callback_data
            for row in user_keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("settings_text_size:user:42:xlarge", user_callbacks)
        self.assertEqual(chat_keyboard.inline_keyboard[0][0].text, "✅ Как у меня")

    def test_chat_title_size_can_override_inherited_design(self):
        user_theme = {
            "style": "custom",
            "background": {"kind": "gradient", "value": "#111,#333"},
            "cache_style": "custom:user:42:1",
            "font": "auto",
            "text_size": "normal",
            "dust_display": "normal",
            "class_art": {"mode": "class", "path": None},
            "personalization_revision": 3,
            "blur": 0,
        }
        chat = _sample_chat(image_style="inherit", image_text_size="large")
        with (
            patch.object(main, "_image_theme_for_user_id", return_value=user_theme),
            patch.object(main, "get_managed_chat", return_value=chat),
        ):
            theme = main._image_theme_for_context(42, chat["chat_id"])
        self.assertEqual(theme["style"], "custom")
        self.assertEqual(theme["text_size"], "large")
        self.assertIn("text:large", theme["cache_style"])

    def test_chat_inheritance_uses_configuring_admin_not_message_sender(self):
        owner_theme = {
            "style": "custom",
            "background": {"kind": "image", "value": "owner.jpg"},
            "cache_style": "custom:user:77:9",
            "font": "belwe",
            "text_size": "xlarge",
            "dust_display": "large",
            "class_art": {"mode": "class", "path": None},
            "personalization_revision": 9,
            "blur": 25,
        }
        sender_theme = {
            **owner_theme,
            "style": "classic",
            "background": None,
            "cache_style": "classic:prefs:1",
            "personalization_revision": 1,
        }
        chat = _sample_chat(added_by=77, image_style="inherit")

        def theme_for_user(user_id):
            return owner_theme if user_id == 77 else sender_theme

        with (
            patch.object(main, "_image_theme_for_user_id", side_effect=theme_for_user),
            patch.object(main, "get_managed_chat", return_value=chat),
        ):
            theme = main._image_theme_for_context(42, chat["chat_id"])

        self.assertEqual(theme["style"], "custom")
        self.assertEqual(theme["background"]["value"], "owner.jpg")
        self.assertIn("custom:user:77:9", theme["cache_style"])

    def test_chat_can_override_every_personalization_option(self):
        chat = _sample_chat(
            image_font="oswald",
            image_text_size="xlarge",
            image_dust_display="hidden",
            class_art_mode="logo",
            custom_logo_path="user_assets/logos/chat.png",
            personalization_revision=8,
            cards_per_row_normal=5,
            cards_per_row_extended=8,
            cards_per_row_highlander=10,
            mana_curve_mode="hidden",
        )
        user_theme = {
            "style": "classic",
            "background": None,
            "cache_style": "classic:prefs:3",
            "font": "auto",
            "text_size": "normal",
            "dust_display": "normal",
            "class_art": {"mode": "class", "path": None},
            "personalization_revision": 3,
            "blur": 0,
        }
        with (
            patch.object(main, "_image_theme_for_user_id", return_value=user_theme),
            patch.object(main, "get_managed_chat", return_value=chat),
        ):
            theme = main._image_theme_for_context(42, chat["chat_id"])
        self.assertEqual(theme["font"], "oswald")
        self.assertEqual(theme["text_size"], "xlarge")
        self.assertEqual(theme["dust_display"], "hidden")
        self.assertEqual(theme["class_art"]["mode"], "logo")
        self.assertEqual(theme["layout"], {
            "normal": 0,
            "extended": 0,
            "highlander": 0,
        })
        self.assertEqual(theme["mana_curve"]["mode"], "hidden")
        self.assertIn("chatprefs:8", theme["cache_style"])

    def test_group_deck_button_layout_is_resolved_without_render_changes(self):
        chat = _sample_chat(deck_button_layout="compact")
        user_theme = {
            "style": "classic",
            "background": None,
            "cache_style": "classic:prefs:1",
            "font": "auto",
            "text_size": "normal",
            "dust_display": "normal",
            "class_art": {"mode": "class", "path": None},
            "layout": {"normal": 0, "extended": 0, "highlander": 0},
            "mana_curve": {"mode": "chart", "path": None},
            "button_layout": "full",
            "personalization_revision": 1,
            "blur": 0,
        }
        with (
            patch.object(main, "_image_theme_for_user_id", return_value=user_theme),
            patch.object(main, "get_managed_chat", return_value=chat),
        ):
            theme = main._image_theme_for_context(42, chat["chat_id"])
        self.assertEqual(theme["button_layout"], "compact")

    def test_gradient_presets_are_two_columns(self):
        keyboard = main._gradient_keyboard("chat", -100123, None)
        self.assertTrue(all(len(row) <= 2 for row in keyboard.inline_keyboard))
        self.assertEqual(
            keyboard.inline_keyboard[-1][0].callback_data,
            "settings_background_back:chat:-100123",
        )

    def test_chat_list_uses_group_link_and_two_columns(self):
        chats = [
            _sample_chat(chat_id=-101, title="Первый"),
            _sample_chat(chat_id=-102, title="Второй"),
            _sample_chat(chat_id=-103, title="Третий"),
            _sample_chat(chat_id=-104, title="Скрытый канал", chat_type="channel"),
        ]
        with patch.object(main, "get_managed_chats_for_user", return_value=chats):
            keyboard = main._managed_chats_keyboard(42)
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 1, 2])
        self.assertIn("startgroup=", keyboard.inline_keyboard[-1][0].url)

    def test_chat_settings_and_commands_are_two_columns(self):
        chat = _sample_chat(
            image_style="custom",
            custom_background_kind="image",
            custom_background_value="user_assets/backgrounds/test.jpg",
            disabled_commands=["arena"],
        )
        chat_keyboard = main._managed_chat_keyboard(chat)
        command_keyboard = main._managed_commands_keyboard(chat)
        self.assertTrue(all(len(row) <= 2 for row in chat_keyboard.inline_keyboard))
        self.assertTrue(
            all(len(row) <= 2 for row in command_keyboard.inline_keyboard)
        )
        callbacks = {
            button.callback_data
            for row in chat_keyboard.inline_keyboard
            for button in row
        }
        self.assertIn(f"settings_chat_fonts:{chat['chat_id']}", callbacks)
        self.assertIn(f"settings_chat_dust:{chat['chat_id']}", callbacks)
        self.assertIn(
            f"settings_chat_class_art:{chat['chat_id']}",
            callbacks,
        )
        self.assertIn(f"settings_chat_designs:{chat['chat_id']}", callbacks)
        self.assertIn(f"settings_chat_buttons:{chat['chat_id']}", callbacks)
        self.assertNotIn(f"settings_rows_menu:chat:{chat['chat_id']}", callbacks)

    def test_deck_button_layouts_match_group_presets(self):
        full = main.build_deck_action_keyboard("AAE", "abc", 7, "full")
        compact = main.build_deck_action_keyboard("AAE", "abc", 7, "compact")
        copy_only = main.build_deck_action_keyboard(
            "AAE", "abc", 7, "copy_only"
        )
        self.assertEqual([len(row) for row in full.inline_keyboard], [1, 2])
        self.assertEqual([len(row) for row in compact.inline_keyboard], [2])
        self.assertEqual([len(row) for row in copy_only.inline_keyboard], [1])
        self.assertFalse(any(
            button.callback_data and button.callback_data.startswith("open_pack:")
            for row in compact.inline_keyboard for button in row
        ))
        self.assertTrue(any(
            button.callback_data and button.callback_data.startswith("save_deck:")
            for button in compact.inline_keyboard[0]
        ))

    def test_only_latest_preview_generation_remains_active(self):
        key, first = main._next_personalization_preview("user", 42)
        same_key, second = main._next_personalization_preview("user", 42)
        self.assertEqual(key, same_key)
        self.assertFalse(
            main._is_latest_personalization_preview(key, first)
        )
        self.assertTrue(
            main._is_latest_personalization_preview(key, second)
        )


class BackgroundUploadTests(unittest.TestCase):
    def test_valid_png_is_normalized_and_saved_as_jpeg(self):
        source = BytesIO()
        Image.new("RGB", (640, 480), "#47302a").save(source, format="PNG")
        project_root = main.PROJECT_ROOT
        with tempfile.TemporaryDirectory(dir=project_root / "tmp_decks") as directory:
            background_dir = Path(directory)
            with patch.object(main, "_BACKGROUND_DIR", background_dir):
                relative = main._save_background_image_sync(
                    source.getvalue(),
                    "user",
                    42,
                )
            saved = project_root / relative
            self.assertTrue(saved.exists())
            with Image.open(saved) as image:
                self.assertEqual(image.format, "JPEG")

    def test_non_image_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "не похож"):
            main._save_background_image_sync(b"not an image", "user", 42)

    def test_tiny_image_is_rejected(self):
        source = BytesIO()
        Image.new("RGB", (100, 100), "white").save(source, format="WEBP")
        with self.assertRaisesRegex(ValueError, "минимум 320"):
            main._save_background_image_sync(source.getvalue(), "user", 42)

    def test_transparent_logo_is_saved_as_png(self):
        source = BytesIO()
        Image.new("RGBA", (256, 256), (120, 40, 220, 128)).save(
            source,
            format="PNG",
        )
        project_root = main.PROJECT_ROOT
        with tempfile.TemporaryDirectory(dir=project_root / "tmp_decks") as directory:
            with patch.object(main, "_LOGO_DIR", Path(directory)):
                relative = main._save_logo_image_sync(
                    source.getvalue(),
                    42,
                )
            saved = project_root / relative
            self.assertTrue(saved.exists())
            with Image.open(saved) as image:
                self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA")


class ManagedChatRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_private_claim_payload_round_trips_negative_chat_id(self):
        payload = main._manage_chat_start_payload(-100987654321)
        self.assertEqual(
            main._manage_chat_id_from_payload(payload),
            -100987654321,
        )

    async def test_startgroup_settings_registers_selected_group(self):
        message = SimpleNamespace(
            text="/start settings",
            chat=SimpleNamespace(
                id=-100987654321,
                type="supergroup",
                title="Новая группа",
                first_name=None,
            ),
            from_user=SimpleNamespace(
                id=42,
                username="admin",
                first_name="Админ",
            ),
            answer=AsyncMock(),
        )
        with (
            patch.object(
                main,
                "_can_manage_chat",
                AsyncMock(return_value=True),
            ),
            patch.object(main, "ensure_bot_user") as ensure_user,
            patch.object(main, "register_managed_chat") as register_chat,
        ):
            await main.process_start_command(message)

        ensure_user.assert_called_once_with(
            42,
            username="admin",
            first_name="Админ",
        )
        register_chat.assert_called_once_with(
            -100987654321,
            "Новая группа",
            "supergroup",
            42,
        )
        message.answer.assert_awaited_once()
        self.assertIn(
            "Чат подключён",
            message.answer.await_args.args[0],
        )

    async def test_anonymous_admin_gets_private_claim_link(self):
        chat = SimpleNamespace(
            id=-100987654321,
            type="supergroup",
            title="Анонимная группа",
            first_name=None,
        )
        message = SimpleNamespace(
            text="/settings",
            chat=chat,
            sender_chat=chat,
            from_user=SimpleNamespace(
                id=main.TELEGRAM_GROUP_ANONYMOUS_BOT_ID,
                username="GroupAnonymousBot",
                first_name="Group",
            ),
            answer=AsyncMock(),
        )
        with patch.object(main, "register_managed_chat") as register_chat:
            connected = await main._connect_group_settings(message)

        self.assertTrue(connected)
        register_chat.assert_called_once_with(
            -100987654321,
            "Анонимная группа",
            "supergroup",
            None,
        )
        button = message.answer.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard[0][0]
        self.assertIn("?start=manage_n100987654321", button.url)

    async def test_private_claim_links_real_administrator(self):
        message = SimpleNamespace(
            text="/start manage_n100987654321",
            chat=SimpleNamespace(
                id=42,
                type="private",
                title=None,
                first_name="Админ",
            ),
            from_user=SimpleNamespace(
                id=42,
                username="admin",
                first_name="Админ",
            ),
            answer=AsyncMock(),
        )
        stored_chat = _sample_chat(
            chat_id=-100987654321,
            title="Анонимная группа",
        )
        with (
            patch.object(
                main,
                "_can_manage_chat",
                AsyncMock(return_value=True),
            ),
            patch.object(
                main,
                "get_managed_chat",
                return_value=stored_chat,
            ),
            patch.object(main, "ensure_bot_user"),
            patch.object(main, "register_managed_chat") as register_chat,
        ):
            await main.process_start_command(message)

        register_chat.assert_called_once_with(
            -100987654321,
            "Анонимная группа",
            "supergroup",
            42,
        )
        self.assertIn(
            "привязан к вашему профилю",
            message.answer.await_args.args[0],
        )

    async def test_webhook_subscribes_to_bot_membership_updates(self):
        main._include_router_once()
        with (
            patch.object(main.asyncio, "sleep", AsyncMock()),
            patch.object(
                main.bot,
                "set_webhook",
                AsyncMock(),
            ) as set_webhook,
        ):
            await main._set_webhook_after_startup()

        allowed = set_webhook.await_args.kwargs["allowed_updates"]
        self.assertIn("my_chat_member", allowed)


class SettingsConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main._PERSONALIZATION_PREVIEW_GENERATION.clear()
        main._META_SWITCH_TASKS.clear()

    async def test_rapid_preview_clicks_render_only_latest_choice(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(),
        )
        with (
            patch.object(
                main,
                "_build_personalization_preview",
                AsyncMock(return_value=b"preview"),
            ) as build,
            patch.object(
                main,
                "get_user_image_settings",
                return_value={},
            ),
            patch.object(
                main,
                "_show_settings_photo",
                AsyncMock(return_value=callback.message),
            ) as show,
        ):
            first = asyncio.create_task(
                main._show_personalization_preview(callback)
            )
            await asyncio.sleep(0.01)
            second = asyncio.create_task(
                main._show_personalization_preview(callback)
            )
            await asyncio.gather(first, second)

        build.assert_awaited_once()
        show.assert_awaited_once()

    async def test_new_meta_switch_cancels_previous_message_task(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(
                chat=SimpleNamespace(id=10),
                message_id=20,
            )
        )
        first_started = asyncio.Event()

        async def first_switch():
            key, current = await main._cancel_previous_meta_switch(callback)
            first_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                main._finish_meta_switch(key, current)

        first = asyncio.create_task(first_switch())
        await first_started.wait()
        key, current = await main._cancel_previous_meta_switch(callback)
        main._finish_meta_switch(key, current)
        result = await asyncio.gather(first, return_exceptions=True)

        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertFalse(main._META_SWITCH_TASKS)


if __name__ == "__main__":
    unittest.main()
