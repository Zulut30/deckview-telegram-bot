from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deckview.workers import jobs


class ApiRenderWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_skips_renderer(self):
        cached = {
            "deck_code": "code",
            "deck_name": "Name",
            "image_style": "parchment",
            "filename": "render-cache/aa/result.jpg",
            "artifact_path": "/cache/result.jpg",
            "cost": 100,
            "deck_class": "Маг",
            "deck_mode": "Стандарт",
            "card_dbf_ids": [1],
        }
        with (
            patch.object(jobs, "init_web_db"),
            patch.object(jobs, "lookup_render_cache", return_value=cached),
            patch.object(jobs, "create_picture", new=AsyncMock()) as create_picture,
            patch.object(jobs, "emit_render_timing"),
        ):
            result = await jobs._render_api_deck_job(
                {
                    "deck_code": "code",
                    "deck_name": "Name",
                    "image_style": "parchment",
                }
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["cached"])
        create_picture.assert_not_awaited()

    async def test_invalid_style_is_rejected_before_render(self):
        with (
            patch.object(jobs, "init_web_db"),
            patch.object(jobs, "create_picture", new=AsyncMock()) as create_picture,
        ):
            result = await jobs._render_api_deck_job(
                {"deck_code": "code", "image_style": "unknown"}
            )

        self.assertEqual(result["error_code"], "INVALID_IMAGE_STYLE")
        create_picture.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
