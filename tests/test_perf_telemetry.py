"""Tests for the sanitized, failure-safe performance event sink."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from deckview.infrastructure.perf_telemetry import emit_render_timing


class PerformanceTelemetryTests(unittest.TestCase):
    def test_event_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "perf.jsonl")
            with patch.dict(
                os.environ,
                {"DECKVIEW_PERF_LOG": "1", "DECKVIEW_PERF_LOG_PATH": path},
            ):
                emitted = emit_render_timing(
                    source="web_api",
                    result="ok",
                    timings={"generator_total_ms": 12.5, "chat_id": 12345},
                    deck_code="private-deck-code",
                    trace_id="trace-1",
                )

            self.assertTrue(emitted)
            with open(path, encoding="utf-8") as stream:
                event = json.loads(stream.readline())
            self.assertEqual(event["generator_total_ms"], 12.5)
            self.assertNotIn("chat_id", event)
            self.assertNotIn("deck_code", event)
            self.assertNotIn("private-deck-code", json.dumps(event))
            self.assertIn("deck_key", event)

    def test_disabled_sink_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "perf.jsonl")
            with patch.dict(
                os.environ,
                {"DECKVIEW_PERF_LOG": "0", "DECKVIEW_PERF_LOG_PATH": path},
            ):
                emitted = emit_render_timing(
                    source="web_api",
                    result="ok",
                    timings={},
                )
            self.assertFalse(emitted)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
