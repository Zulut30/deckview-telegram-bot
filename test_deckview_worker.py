import os
import unittest
from unittest.mock import patch

import deckview_worker


class DeckviewWorkerTests(unittest.TestCase):
    def test_worker_process_default_uses_available_cpu(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(deckview_worker.os, "cpu_count", return_value=16),
        ):
            self.assertEqual(deckview_worker._worker_processes(), 4)

    def test_worker_process_setting_is_safely_clamped(self):
        with patch.dict(os.environ, {"DECKVIEW_WORKER_PROCESSES": "99"}):
            self.assertEqual(deckview_worker._worker_processes(), 8)
        with patch.dict(os.environ, {"DECKVIEW_WORKER_PROCESSES": "0"}):
            self.assertEqual(deckview_worker._worker_processes(), 1)

    def test_card_catalog_is_preloaded_once_before_fork(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DECKVIEW_WORKER_PRELOAD_CARDS": "1",
                    "DECKVIEW_WORKER_PRELOAD_TIMEOUT_SECONDS": "1.5",
                },
            ),
            patch.object(deckview_worker, "hsjson_configure") as configure,
            patch.object(
                deckview_worker,
                "ensure_hsjson_loaded",
                return_value=True,
            ) as ensure_loaded,
            patch.object(
                deckview_worker,
                "preload_snapshot",
                return_value=6493,
            ) as preload_snapshot,
            patch.object(
                deckview_worker,
                "preload_local_arena_image_index",
                return_value=8118,
            ) as preload_images,
            patch.object(deckview_worker, "reset_http_session") as reset_session,
        ):
            result = deckview_worker._preload_shared_card_catalog()

        self.assertTrue(result)
        configure.assert_called_once_with(
            deckview_worker.HSJSON_CARDS_URL,
            deckview_worker.HSJSON_LOCALE,
        )
        ensure_loaded.assert_called_once_with(timeout_seconds=1.5)
        preload_snapshot.assert_called_once_with()
        preload_images.assert_called_once_with()
        reset_session.assert_called_once_with()

    def test_card_catalog_preload_can_be_disabled(self):
        with (
            patch.dict(os.environ, {"DECKVIEW_WORKER_PRELOAD_CARDS": "0"}),
            patch.object(deckview_worker, "ensure_hsjson_loaded") as ensure_loaded,
            patch.object(deckview_worker, "preload_snapshot") as preload_snapshot,
            patch.object(
                deckview_worker,
                "preload_local_arena_image_index",
            ) as preload_images,
        ):
            result = deckview_worker._preload_shared_card_catalog()

        self.assertFalse(result)
        ensure_loaded.assert_not_called()
        preload_snapshot.assert_not_called()
        preload_images.assert_not_called()


if __name__ == "__main__":
    unittest.main()
