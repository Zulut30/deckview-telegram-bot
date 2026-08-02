from __future__ import annotations

import time
from typing import Any

from config import (
    DECKVIEW_QUEUE_ENABLED,
    DECKVIEW_QUEUE_JOB_TIMEOUT,
    DECKVIEW_QUEUE_NAME,
    DECKVIEW_REDIS_URL,
)

try:
    from redis import Redis
    from rq import Queue
except Exception as exc:  # pragma: no cover - exercised on hosts without RQ deps.
    Redis = None
    Queue = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

_QUEUE = None


def _queue():
    global _QUEUE
    if not DECKVIEW_QUEUE_ENABLED:
        return None
    if Redis is None or Queue is None:
        print(f"[Deckview queue] RQ unavailable: {_IMPORT_ERROR}")
        return None
    if _QUEUE is None:
        connection = Redis.from_url(
            DECKVIEW_REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=2,
        )
        connection.ping()
        _QUEUE = Queue(DECKVIEW_QUEUE_NAME, connection=connection)
    return _QUEUE


def queue_available() -> bool:
    try:
        return _queue() is not None
    except Exception as exc:
        print(f"[Deckview queue] Redis unavailable: {exc}")
        return False


def enqueue_deck_render(payload: dict[str, Any]) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        queued_payload = dict(payload)
        # RQ 2.x serializes enqueued_at with second precision. Keep a precise
        # enqueue timestamp in the payload so queue latency telemetry is useful.
        queued_payload["_queued_at_ns"] = time.time_ns()
        job = q.enqueue(
            "deckview_jobs.render_deck_message_job",
            queued_payload,
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue deck render: {exc}")
        return None


def enqueue_hsguru_publish(payload: dict[str, Any], *, to_telegram: bool) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        clean_payload = dict(payload)
        clean_payload.pop("_deck", None)
        job = q.enqueue(
            "deckview_jobs.publish_hsguru_payload_job",
            clean_payload,
            bool(to_telegram),
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue HSGuru publish: {exc}")
        return None


def enqueue_hsguru_cycle(*, to_telegram: bool) -> str | None:
    try:
        q = _queue()
        if q is None:
            return None
        job = q.enqueue(
            "deckview_jobs.publish_hsguru_cycle_job",
            bool(to_telegram),
            job_timeout=DECKVIEW_QUEUE_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id
    except Exception as exc:
        print(f"[Deckview queue] Failed to enqueue HSGuru cycle: {exc}")
        return None
