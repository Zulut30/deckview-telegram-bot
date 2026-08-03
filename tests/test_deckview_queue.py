import unittest
from unittest.mock import Mock, patch

from deckview.workers import queue as deckview_queue


class DeckviewQueueTests(unittest.TestCase):
    def test_enqueue_adds_precise_timestamp_without_mutating_payload(self):
        queue = Mock()
        queue.enqueue.return_value.id = "job-1"
        payload = {"deck_code": "AA-test"}

        with (
            patch.object(deckview_queue, "_queue", return_value=queue),
            patch.object(deckview_queue.time, "time_ns", return_value=123456789),
        ):
            result = deckview_queue.enqueue_deck_render(payload)

        self.assertEqual(result, "job-1")
        self.assertNotIn("_queued_at_ns", payload)
        queued_payload = queue.enqueue.call_args.args[1]
        self.assertEqual(queued_payload["_queued_at_ns"], 123456789)


if __name__ == "__main__":
    unittest.main()
