from __future__ import annotations

import os
import time
from pathlib import Path

from redis import Redis
from rq import Queue, SimpleWorker
from rq.worker_pool import WorkerPool

from deckview.config import (
    DECKVIEW_QUEUE_NAME,
    DECKVIEW_REDIS_URL,
    HSJSON_CARDS_URL,
    HSJSON_LOCALE,
)
from framework.hearthstonejson_api import configure as hsjson_configure
from framework.hearthstonejson_api import ensure_loaded as ensure_hsjson_loaded
from framework.grequests_downloader import preload_local_arena_image_index
from framework.http_session import reset_http_session
from image_creator.card_catalog_snapshot import preload_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _worker_processes() -> int:
    default = min(4, os.cpu_count() or 1)
    try:
        configured = int(os.getenv("DECKVIEW_WORKER_PROCESSES", str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(1, min(configured, 8))


def _preload_shared_card_catalog() -> bool:
    """Load read-mostly card metadata once before the worker pool forks."""
    enabled = os.getenv("DECKVIEW_WORKER_PRELOAD_CARDS", "1").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    try:
        timeout = max(
            0.25,
            float(
                os.getenv("DECKVIEW_WORKER_PRELOAD_TIMEOUT_SECONDS", "5")
                .replace(",", ".")
            ),
        )
    except (TypeError, ValueError):
        timeout = 5.0

    started = time.perf_counter()
    snapshot_cards = 0
    arena_images = 0
    loaded = False
    try:
        snapshot_cards = preload_snapshot()
    except Exception as exc:
        print(
            "[Deckview worker] Kolodahs snapshot preload fallback: "
            f"{type(exc).__name__}"
        )
    try:
        arena_images = preload_local_arena_image_index()
    except Exception as exc:
        print(
            "[Deckview worker] Arena image index preload fallback: "
            f"{type(exc).__name__}"
        )
    try:
        hsjson_configure(HSJSON_CARDS_URL, HSJSON_LOCALE)
        loaded = ensure_hsjson_loaded(timeout_seconds=timeout)
    except Exception as exc:
        print(
            "[Deckview worker] Shared card preload fallback: "
            f"{type(exc).__name__}"
        )
    finally:
        # A requests connection pool must never be shared across forked
        # processes. Each child creates its own pool lazily after startup.
        reset_http_session()
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(
        "[Deckview worker] Shared render sources "
        f"snapshot={snapshot_cards}, arena_images={arena_images}, "
        f"hsjson={'ready' if loaded else 'unavailable'} "
        f"in {elapsed_ms:.1f} ms"
    )
    return loaded


def main() -> None:
    os.chdir(PROJECT_ROOT)

    # Import the heavy render stack once before the worker processes are forked.
    # Linux can then share the read-only module pages between workers, while each
    # SimpleWorker keeps its HTTP sessions and in-memory card caches between jobs.
    __import__("deckview.workers.jobs")
    _preload_shared_card_catalog()

    connection = Redis.from_url(DECKVIEW_REDIS_URL)
    queue = Queue(DECKVIEW_QUEUE_NAME, connection=connection)
    processes = _worker_processes()
    pool = WorkerPool(
        [queue],
        connection=connection,
        num_workers=processes,
        worker_class=SimpleWorker,
    )
    print(
        f"[Deckview worker] Listening on RQ queue '{DECKVIEW_QUEUE_NAME}' "
        f"with {processes} persistent processes"
    )
    pool.start()


if __name__ == "__main__":
    main()
