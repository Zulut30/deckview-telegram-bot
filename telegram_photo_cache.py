"""Redis-backed cache for Telegram photo file identifiers."""

from __future__ import annotations

import os
import threading

from config import DECKVIEW_REDIS_URL

try:
    from redis import Redis
except Exception:  # pragma: no cover - delivery falls back to uploading a file.
    Redis = None


_client = None
_client_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv("DECKVIEW_TELEGRAM_FILE_ID_CACHE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _redis_client():
    global _client
    if Redis is None or not _enabled():
        return None
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Redis.from_url(
                    DECKVIEW_REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                )
    return _client


def _key(render_cache_key: str) -> str:
    return f"deckview:telegram-photo:{render_cache_key.strip()}"


def get_telegram_photo_file_id(render_cache_key: str | None) -> str | None:
    if not render_cache_key:
        return None
    try:
        client = _redis_client()
        value = client.get(_key(render_cache_key)) if client is not None else None
        if not value:
            return None
        return str(value).strip() or None
    except Exception:
        return None


def store_telegram_photo_file_id(
    render_cache_key: str | None,
    file_id: str | None,
) -> bool:
    if not render_cache_key or not file_id:
        return False
    try:
        client = _redis_client()
        if client is None:
            return False
        ttl_seconds = max(
            3600,
            int(os.getenv("DECKVIEW_TELEGRAM_FILE_ID_TTL_SECONDS", "2592000")),
        )
        return bool(client.setex(_key(render_cache_key), ttl_seconds, file_id))
    except Exception:
        return False


def delete_telegram_photo_file_id(render_cache_key: str | None) -> None:
    if not render_cache_key:
        return
    try:
        client = _redis_client()
        if client is not None:
            client.delete(_key(render_cache_key))
    except Exception:
        pass
