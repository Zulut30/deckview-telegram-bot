from __future__ import annotations

import unittest
from unittest.mock import patch

from deckview.services.deck_download_service import (
    build_download_reference,
    decode_render_download_reference,
    encode_render_download_reference,
    resolve_cached_download,
)


class DeckDownloadServiceTests(unittest.TestCase):
    def test_cache_key_round_trip_fits_telegram_callback_limit(self):
        cache_key = "ab" * 32
        reference = encode_render_download_reference(cache_key)

        self.assertEqual(len(reference), 43)
        self.assertEqual(decode_render_download_reference(reference), cache_key)
        self.assertLessEqual(len(f"open_pack:{reference}".encode("utf-8")), 64)

    def test_invalid_reference_fails_closed(self):
        self.assertIsNone(encode_render_download_reference("not-a-cache-key"))
        self.assertIsNone(decode_render_download_reference("../escape"))

    def test_build_reference_prefers_persistent_cache(self):
        cache_key = "12" * 32
        reference = build_download_reference(
            {"cache_key": cache_key},
            fallback_reference="legacy123",
        )
        self.assertEqual(decode_render_download_reference(reference), cache_key)
        self.assertEqual(
            build_download_reference(None, fallback_reference="legacy123"),
            "legacy123",
        )

    @patch("deckview.services.deck_download_service.lookup_render_cache_by_key")
    def test_resolve_cached_download_uses_persistent_artifact(self, lookup):
        cache_key = "34" * 32
        lookup.return_value = {"artifact_path": "/cache/deck.jpg"}

        resolved = resolve_cached_download(
            encode_render_download_reference(cache_key)
        )

        self.assertEqual(resolved, "/cache/deck.jpg")
        lookup.assert_called_once_with(cache_key)
