"""Contract tests for the instrumented public render API."""

import os
import unittest
from unittest.mock import MagicMock, patch

from deckview.web import application as web_app


class WebApiPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()

    def test_health_offers_corresponding_source(self):
        response = self.client.get("/deckview-api/v1/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["license"], "AGPL-3.0-or-later")
        self.assertEqual(payload["source_code"], web_app.DECKVIEW_SOURCE_CODE_URL)

    def test_generated_render_cache_assets_are_immutable(self):
        response = self.client.get(
            "/static/generated/render-cache/missing/preview.webp",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )

    def test_cache_hit_skips_generation_and_emits_timing(self):
        cached = {
            "filename": "cached.jpg",
            "cost": 100,
            "deck_code": "code",
            "deck_name": None,
        }
        with (
            patch.dict(os.environ, {"DECKVIEW_LEGACY_RENDER_CACHE_READ": "1"}),
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

    def test_versioned_cache_miss_skips_legacy_cache_by_default(self):
        image = MagicMock()

        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None),
            patch.object(web_app, "find_cached") as legacy,
            patch.object(
                web_app,
                "_run_create_picture",
                return_value=(image, 100, "Маг", "Стандарт", [95650, 69674]),
            ),
            patch.object(web_app, "add_generated", return_value=1),
            patch.object(web_app, "add_deck_cards"),
            patch.object(web_app, "store_render_cache", return_value=None),
            patch.object(web_app, "write_rendered_jpeg", return_value=False),
            patch.object(web_app, "emit_render_timing"),
        ):
            os.environ.pop("DECKVIEW_LEGACY_RENDER_CACHE_READ", None)
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["cached"])
        legacy.assert_not_called()

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
            patch.object(web_app, "write_rendered_jpeg", return_value=False) as write_jpeg,
            patch.object(web_app, "emit_render_timing") as emit,
        ):
            response = self.client.post(
                "/deckview-api/v1/render",
                json={"deck_code": "code"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["cached"])
        write_jpeg.assert_called_once()
        self.assertEqual(write_jpeg.call_args.kwargs["quality"], 90)
        self.assertFalse(write_jpeg.call_args.kwargs["optimize"])
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
            "preview_filename": "render-cache/ab/key.preview-v1.webp",
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
        self.assertIn(
            "/static/generated/render-cache/ab/key.preview-v1.webp",
            response.get_json()["preview_image_url"],
        )
        legacy.assert_not_called()
        create_picture.assert_not_called()
        self.assertEqual(emit.call_args.kwargs["timings"]["cache_status"], "render_cache_hit")

    def test_async_cache_miss_queues_without_blocking_http_worker(self):
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(web_app, "lookup_render_cache", return_value=None),
            patch.object(web_app, "find_cached", return_value=None),
            patch.object(web_app, "build_render_cache_key", return_value="a" * 64),
            patch.object(web_app, "enqueue_api_render", return_value=f"api-render-{'a' * 64}") as enqueue,
            patch.object(web_app, "_run_create_picture") as create_picture,
        ):
            response = self.client.post(
                "/deckview-api/v1/render/parchment",
                headers={"Prefer": "respond-async"},
                json={"deck_code": "code", "deck_name": "Name"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["state"], "queued")
        self.assertTrue(payload["status_url"].endswith(payload["job_id"]))
        self.assertEqual(enqueue.call_args.args[0]["image_style"], "parchment")
        create_picture.assert_not_called()

    def test_async_request_still_returns_cache_hit_immediately(self):
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
            patch.object(web_app.os.path, "isfile", return_value=True),
            patch.object(web_app, "enqueue_api_render") as enqueue,
            patch.object(web_app, "emit_render_timing"),
        ):
            response = self.client.post(
                "/deckview-api/v1/render/parchment",
                headers={"Prefer": "respond-async"},
                json={"deck_code": "code", "deck_name": "Name"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cached"])
        enqueue.assert_not_called()

    def test_async_job_status_returns_finished_image(self):
        job_id = f"api-render-{'b' * 64}"
        with (
            patch.object(web_app, "_require_deckview_api_auth", return_value=None),
            patch.object(
                web_app,
                "api_render_job_snapshot",
                return_value={
                    "job_id": job_id,
                    "state": "finished",
                    "result": {
                        "success": True,
                        "filename": "render-cache/bb/result.jpg",
                        "image_style": "parchment",
                        "deck_code": "code",
                        "cost": 100,
                        "preview_filename": "render-cache/bb/result.preview-v1.webp",
                    },
                },
            ),
        ):
            response = self.client.get(f"/deckview-api/v1/render/jobs/{job_id}")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["state"], "done")
        self.assertIn("/static/generated/render-cache/bb/result.jpg", payload["image_url"])
        self.assertIn(
            "/static/generated/render-cache/bb/result.preview-v1.webp",
            payload["preview_image_url"],
        )


if __name__ == "__main__":
    unittest.main()
