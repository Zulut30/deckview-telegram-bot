"""Contract tests for Telegram RQ timing and render-cache boundaries."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from PIL import Image

from deckview.workers import jobs as deckview_jobs


class TelegramWorkerPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_job_emits_complete_timing(self):
        observed = {}
        delivery_events = []

        async def send_photo(*_args, **_kwargs):
            delivery_events.append("send_photo")
            return SimpleNamespace(
                photo=[SimpleNamespace(file_id="new-telegram-photo-id")]
            )

        async def delete_message(*_args, **_kwargs):
            delivery_events.append("delete_status")

        bot = SimpleNamespace(
            delete_message=AsyncMock(side_effect=delete_message),
            send_photo=AsyncMock(side_effect=send_photo),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )

        async def create_picture(
            _code,
            deck_name=None,
            timings=None,
            image_style="classic",
            image_text_size="normal",
            image_dust_display="normal",
            image_class_art=None,
            image_layout=None,
            image_mana_curve=None,
            **_kwargs,
        ):
            await asyncio.sleep(0)
            observed["archetype_overlapped_render"] = observed.get(
                "archetype_started", False
            )
            observed["image_style"] = image_style
            observed["image_text_size"] = image_text_size
            observed["image_dust_display"] = image_dust_display
            observed["image_class_art"] = image_class_art
            observed["image_layout"] = image_layout
            observed["image_mana_curve"] = image_mana_curve
            timings.update(
                {
                    "generator_result": "ok",
                    "generator_total_ms": 10.0,
                    "image_compose_ms": 5.0,
                }
            )
            return Image.new("RGB", (32, 32)), 100, "Маг", "Стандарт", [123]

        async def recognize_archetype(_code):
            observed["archetype_started"] = True
            return {"success": False}

        payload = {
            "deck_code": "code",
            "deck_name": None,
            "chat_id": 1,
            "chat_type": "private",
            "source": "test",
            "reply_to_message_id": 99,
            "status_chat_id": 1,
            "status_message_id": 98,
            "image_style": "parchment",
            "image_text_size": "large",
            "theme_revision": 6,
            "cache_style": "parchment:prefs:6",
            "user": {"id": 42, "username": "tester", "first_name": "Test"},
        }
        current_settings = {
            "style": "classic",
            "font": "auto",
            "text_size": "large",
            "dust_display": "hidden",
            "class_art_mode": "logo",
            "custom_logo_path": "user_assets/logos/test.png",
            "personalization_revision": 7,
            "background_kind": None,
            "background_value": None,
            "blur": 0,
            "cards_per_row_normal": 5,
            "cards_per_row_extended": 8,
            "cards_per_row_highlander": 10,
            "mana_curve_mode": "hidden",
            "mana_curve_image_path": None,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(
                    deckview_jobs,
                    "get_user_image_settings",
                    return_value=current_settings,
                ),
                patch.object(deckview_jobs, "lookup_render_cache", return_value=None),
                patch.object(deckview_jobs, "create_picture", side_effect=create_picture),
                patch.object(
                    deckview_jobs,
                    "_recognize_archetype",
                    side_effect=recognize_archetype,
                ),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "store_render_cache", return_value=None),
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await deckview_jobs._render_deck_message_job(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(observed["image_style"], "classic")
        self.assertEqual(observed["image_text_size"], "large")
        self.assertEqual(observed["image_dust_display"], "hidden")
        self.assertEqual(
            observed["image_class_art"]["path"],
            "user_assets/logos/test.png",
        )
        self.assertIsNone(observed["image_layout"])
        self.assertEqual(observed["image_mana_curve"]["mode"], "hidden")
        self.assertTrue(observed["archetype_overlapped_render"])
        bot.send_photo.assert_awaited_once()
        result_rows = bot.send_photo.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard
        self.assertEqual([len(row) for row in result_rows], [1, 2])
        self.assertEqual(result_rows[0][0].text, "Скопировать код")
        self.assertEqual(result_rows[1][0].text, "Скачать")
        self.assertEqual(
            result_rows[1][0].icon_custom_emoji_id,
            "5879883461711367869",
        )
        self.assertEqual(result_rows[1][1].text, "Сохранить")
        self.assertEqual(
            bot.send_photo.await_args.kwargs["reply_to_message_id"],
            99,
        )
        self.assertEqual(delivery_events, ["send_photo", "delete_status"])
        bot.session.close.assert_awaited_once()
        self.assertEqual(emit.call_args.kwargs["result"], "ok")
        timings = emit.call_args.kwargs["timings"]
        for key in ("generator_total_ms", "archetype_ms", "jpeg_ms", "db_ms", "delivery_ms", "status_delete_ms", "handler_total_ms"):
            self.assertIn(key, timings)

    async def test_cache_hit_skips_render_and_jpeg(self):
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_photo=AsyncMock(
                return_value=SimpleNamespace(
                    photo=[SimpleNamespace(file_id="new-telegram-photo-id")]
                )
            ),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        cache_entry = {
            "cache_key": "b" * 64,
            "artifact_path": "/cache/deck.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [123],
        }
        payload = {
            "deck_code": "code",
            "deck_name": None,
            "chat_id": 1,
            "chat_type": "private",
            "source": "test",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            def materialize(_entry, destination):
                Path(destination).write_bytes(b"jpeg")
                return destination

            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(deckview_jobs, "lookup_render_cache", return_value=cache_entry),
                patch.object(
                    deckview_jobs,
                    "get_telegram_photo_file_id",
                    return_value=None,
                ),
                patch.object(deckview_jobs, "materialize_render_cache", side_effect=materialize),
                patch.object(deckview_jobs, "create_picture") as create_picture,
                patch.object(deckview_jobs, "_recognize_archetype", AsyncMock(return_value={"success": False})),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "store_render_cache") as store,
                patch.object(
                    deckview_jobs,
                    "store_telegram_photo_file_id",
                ) as store_photo,
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await deckview_jobs._render_deck_message_job(payload)

        self.assertTrue(result["ok"])
        create_picture.assert_not_called()
        store.assert_not_called()
        store_photo.assert_called_once_with(
            "b" * 64,
            "new-telegram-photo-id",
        )
        bot.send_photo.assert_awaited_once()
        timings = emit.call_args.kwargs["timings"]
        self.assertEqual(timings["cache_status"], "render_cache_hit")
        self.assertNotIn("jpeg_ms", timings)

    async def test_telegram_file_id_hit_uses_cached_delivery(self):
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        cache_entry = {
            "cache_key": "a" * 64,
            "artifact_path": "/cache/deck.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [123],
        }
        payload = {
            "deck_code": "code",
            "deck_name": None,
            "chat_id": 1,
            "chat_type": "private",
            "source": "test",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(
                    deckview_jobs,
                    "lookup_render_cache",
                    return_value=cache_entry,
                ),
                patch.object(
                    deckview_jobs,
                    "get_telegram_photo_file_id",
                    return_value="telegram-photo-id",
                ),
                patch.object(deckview_jobs, "materialize_render_cache") as materialize,
                patch.object(deckview_jobs, "create_picture") as create_picture,
                patch.object(
                    deckview_jobs,
                    "_recognize_archetype",
                    AsyncMock(return_value={"success": False}),
                ),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await deckview_jobs._render_deck_message_job(payload)

        self.assertTrue(result["ok"])
        create_picture.assert_not_called()
        materialize.assert_not_called()
        self.assertEqual(bot.send_photo.await_args.args[1], "telegram-photo-id")
        timings = emit.call_args.kwargs["timings"]
        self.assertEqual(timings["cache_status"], "telegram_file_id_hit")

    async def test_stale_telegram_file_id_materializes_only_for_retry(self):
        stale = TelegramBadRequest(
            method=SimpleNamespace(),
            message="Bad Request: wrong file identifier",
        )
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_photo=AsyncMock(
                side_effect=[
                    stale,
                    SimpleNamespace(
                        photo=[SimpleNamespace(file_id="fresh-photo-id")]
                    ),
                ]
            ),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        cache_entry = {
            "cache_key": "c" * 64,
            "artifact_path": "/cache/deck.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [123],
        }
        payload = {
            "deck_code": "code",
            "deck_name": None,
            "chat_id": 1,
            "chat_type": "private",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            def materialize(_entry, destination):
                Path(destination).write_bytes(b"jpeg")
                return destination

            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(deckview_jobs, "lookup_render_cache", return_value=cache_entry),
                patch.object(deckview_jobs, "get_telegram_photo_file_id", return_value="stale-photo-id"),
                patch.object(deckview_jobs, "materialize_render_cache", side_effect=materialize) as materialize_mock,
                patch.object(deckview_jobs, "delete_telegram_photo_file_id") as delete_photo,
                patch.object(deckview_jobs, "store_telegram_photo_file_id") as store_photo,
                patch.object(deckview_jobs, "_recognize_archetype", AsyncMock(return_value={"success": False})),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await deckview_jobs._render_deck_message_job(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(bot.send_photo.await_count, 2)
        materialize_mock.assert_called_once()
        delete_photo.assert_called_once_with("c" * 64)
        store_photo.assert_called_once_with("c" * 64, "fresh-photo-id")
        timings = emit.call_args.kwargs["timings"]
        self.assertEqual(timings["cache_status"], "telegram_file_id_stale")
        self.assertIn("stale_file_materialize_ms", timings)

    async def test_precise_queue_timestamp_is_used(self):
        payload = {
            "deck_code": "code",
            "chat_id": 1,
            "chat_type": "private",
            "_queued_at_ns": 1_000_000_000,
        }
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        cache_entry = {
            "artifact_path": "/cache/deck.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [123],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            def materialize(_entry, destination):
                Path(destination).write_bytes(b"jpeg")
                return destination

            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs.time, "time_ns", return_value=1_025_000_000),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(deckview_jobs, "lookup_render_cache", return_value=cache_entry),
                patch.object(deckview_jobs, "materialize_render_cache", side_effect=materialize),
                patch.object(deckview_jobs, "_recognize_archetype", AsyncMock(return_value={"success": False})),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await deckview_jobs._render_deck_message_job(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(
            emit.call_args.kwargs["timings"]["queue_wait_ms"],
            25.0,
        )

    async def test_slow_archetype_does_not_hold_ready_image(self):
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        cache_entry = {
            "artifact_path": "/cache/deck.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [123],
        }
        payload = {
            "deck_code": "slow-archetype",
            "chat_id": 1,
            "chat_type": "private",
        }

        async def slow_archetype(_code):
            await asyncio.sleep(30)
            return {"success": True, "archetype": "Too late"}

        with tempfile.TemporaryDirectory() as temp_dir:
            def materialize(_entry, destination):
                Path(destination).write_bytes(b"jpeg")
                return destination

            with (
                patch.object(deckview_jobs, "_TMP_DIR", temp_dir),
                patch.object(deckview_jobs, "_ARCHETYPE_BUDGET_SECONDS", 0.001),
                patch.object(deckview_jobs, "_build_bot", return_value=bot),
                patch.object(deckview_jobs, "get_current_job", return_value=None),
                patch.object(deckview_jobs, "init_ratings_db"),
                patch.object(deckview_jobs, "init_web_db"),
                patch.object(deckview_jobs, "lookup_render_cache", return_value=cache_entry),
                patch.object(deckview_jobs, "materialize_render_cache", side_effect=materialize),
                patch.object(deckview_jobs, "_recognize_archetype", side_effect=slow_archetype),
                patch.object(deckview_jobs, "add_generated_with_cards", return_value=1),
                patch.object(deckview_jobs, "add_bot_event"),
                patch.object(deckview_jobs, "emit_render_timing") as emit,
            ):
                result = await asyncio.wait_for(
                    deckview_jobs._render_deck_message_job(payload),
                    timeout=1,
                )

        self.assertTrue(result["ok"])
        bot.send_photo.assert_awaited_once()
        self.assertTrue(
            emit.call_args.kwargs["timings"]["archetype_budget_exceeded"]
        )


if __name__ == "__main__":
    unittest.main()
