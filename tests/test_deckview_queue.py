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

    def test_api_render_uses_deterministic_job_id_and_coalesces(self):
        queue = Mock()
        queue.fetch_job.return_value = None
        queue.enqueue.return_value.id = "api-render-key"
        payload = {"deck_code": "AA-test", "image_style": "parchment"}

        with (
            patch.object(deckview_queue, "_api_queue", return_value=queue),
            patch.object(deckview_queue.time, "time_ns", return_value=99),
        ):
            result = deckview_queue.enqueue_api_render(
                payload,
                job_id="api-render-key",
            )

        self.assertEqual(result, "api-render-key")
        self.assertEqual(queue.enqueue.call_args.kwargs["job_id"], "api-render-key")
        self.assertEqual(queue.enqueue.call_args.args[0], "deckview.workers.jobs.render_api_deck_job")
        self.assertNotIn("_queued_at_ns", payload)

    def test_api_render_returns_existing_active_job(self):
        queue = Mock()
        existing = Mock(id="api-render-key")
        existing.get_status.return_value = "started"
        queue.fetch_job.return_value = existing

        with patch.object(deckview_queue, "_api_queue", return_value=queue):
            result = deckview_queue.enqueue_api_render(
                {"deck_code": "AA-test"},
                job_id="api-render-key",
            )

        self.assertEqual(result, "api-render-key")
        queue.enqueue.assert_not_called()

    def test_api_render_requeues_finished_job_after_cache_miss(self):
        queue = Mock()
        existing = Mock(id="api-render-key")
        existing.get_status.return_value = "finished"
        queue.fetch_job.return_value = existing
        queue.enqueue.return_value.id = "api-render-key"

        with patch.object(deckview_queue, "_api_queue", return_value=queue):
            result = deckview_queue.enqueue_api_render(
                {"deck_code": "AA-test"},
                job_id="api-render-key",
            )

        self.assertEqual(result, "api-render-key")
        existing.delete.assert_called_once_with()
        queue.enqueue.assert_called_once()

    def test_api_job_snapshot_hides_failure_details(self):
        queue = Mock()
        job = Mock(id="api-render-key", result={"success": True})
        job.get_status.return_value = "finished"
        queue.fetch_job.return_value = job

        with patch.object(deckview_queue, "_api_queue", return_value=queue):
            snapshot = deckview_queue.api_render_job_snapshot("api-render-key")

        self.assertEqual(snapshot["state"], "finished")
        self.assertEqual(snapshot["result"], {"success": True})


if __name__ == "__main__":
    unittest.main()
