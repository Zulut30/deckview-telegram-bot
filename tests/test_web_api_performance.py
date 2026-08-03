"""Contract tests for the instrumented public render API."""

import unittest
from unittest.mock import MagicMock, patch

from deckview.web import application as web_app


class WebApiPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()

    def test_cache_hit_skips_generation_and_emits_timing(self):
        cached = {
            "filename": "cached.jpg",
            "cost": 100,
            "deck_code": "code",
            "deck_name": None,
        }
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None),
            patch.object(web_app, "find_cached", return_value=cached),
            patch.object(web_app.os.path, "isfile", return_value=True),
            patch.object(web_app, "_run_create_picture") as create_picture,
            patch.object(web_app, "emit_render_timing") as emit,
        ):
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cached"])
        create_picture.assert_not_called()
        self.assertEqual(emit.call_args.kwargs["timings"]["cache_status"], "legacy_hit")

    def test_cache_miss_preserves_jpeg_settings(self):
        image = MagicMock()

        def create_picture(_code, _name, timings=None, *, image_style="classic"):
            self.assertEqual(image_style, "classic")
            timings.update({"generator_total_ms": 1.0, "generator_result": "ok"})
            return image, 100, "Маг", "Стандарт", [123]

        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None),
            patch.object(web_app, "find_cached", return_value=None),
            patch.object(web_app, "_run_create_picture", side_effect=create_picture),
            patch.object(web_app, "add_generated", return_value=1),
            patch.object(web_app, "add_deck_cards"),
            patch.object(web_app, "store_render_cache", return_value=None),
            patch.object(web_app, "emit_render_timing") as emit,
        ):
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["cached"])
        image.save.assert_called_once()
        self.assertEqual(image.save.call_args.kwargs["format"], "JPEG")
        self.assertEqual(image.save.call_args.kwargs["quality"], 90)
        self.assertFalse(image.save.call_args.kwargs["optimize"])
        self.assertEqual(emit.call_args.kwargs["result"], "ok")

    def test_parchment_endpoint_uses_isolated_style_cache(self):
        image = MagicMock()

        def create_picture(_code, _name, timings=None, *, image_style="classic"):
            self.assertEqual(image_style, "parchment")
            return image, 100, "Жрец", "Стандарт", [123]

        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None) as lookup,
            patch.object(web_app, "find_cached") as legacy,
            patch.object(web_app, "_run_create_picture", side_effect=create_picture),
            patch.object(web_app, "add_generated", return_value=1),
            patch.object(web_app, "add_deck_cards"),
            patch.object(web_app, "store_render_cache", return_value=None) as store,
            patch.object(web_app, "emit_render_timing"),
        ):
            response = self.client.post(
                "/deckview-api/v1/render/parchment",
                json={"deck_code": "code", "deck_name": "Name"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["image_style"], "parchment")
        self.assertIn("latency_ms", payload)
        self.assertIn("Server-Timing", response.headers)
        legacy.assert_not_called()
        self.assertEqual(lookup.call_args.kwargs["image_style"], "parchment")
        self.assertEqual(store.call_args.kwargs["image_style"], "parchment")

    def test_render_rejects_unknown_style(self):
        with patch.object(web_app, "_require_deckview_api_auth", return_value=None):
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code", "image_style": "wood"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "INVALID_IMAGE_STYLE")

    def test_render_cache_hit_precedes_legacy_cache(self):
        cached = {
            "deck_code": "code",
            "deck_name": "Name",
            "filename": "render-cache/ab/key.jpg",
            "artifact_path": "/cache/key.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
        }
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=cached),
            patch.object(web_app, "find_cached") as legacy,
            patch.object(web_app.os.path, "isfile", return_value=True),
            patch.object(web_app, "_run_create_picture") as create_picture,
            patch.object(web_app, "emit_render_timing") as emit,
        ):
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code", "deck_name": "Name"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cached"])
        self.assertEqual(response.get_json()["deck_class"], "Маг")
        legacy.assert_not_called()
        create_picture.assert_not_called()
        self.assertEqual(emit.call_args.kwargs["timings"]["cache_status"], "render_cache_hit")


if __name__ == "__main__":
    unittest.main()
