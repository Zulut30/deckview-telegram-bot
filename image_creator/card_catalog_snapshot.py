"""Fail-open, hot-reloadable local snapshot of Kolodahs card metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_VERSION = 1
_snapshot_lock = threading.RLock()
_snapshot_signature: tuple[Any, ...] | None = None
_snapshot_cards: dict[int, dict[str, Any]] = {}
_snapshot_standard_dbf_ids: set[int] = set()
_SNAPSHOT_FIELDS = (
    "card_id",
    "name",
    "mana",
    "rarity",
    "collectible",
    "player_class",
    "type",
    "image_url",
)


def snapshot_enabled() -> bool:
    return os.getenv("DECKVIEW_CARD_SNAPSHOT", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def snapshot_path() -> Path:
    raw = os.getenv(
        "DECKVIEW_CARD_SNAPSHOT_PATH",
        str(_PROJECT_ROOT / "cache" / "card-catalog" / "cards-current.json"),
    )
    path = Path(raw)
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _file_signature(path: Path) -> tuple[Any, ...]:
    stat = path.stat()
    return (
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_size,
    )


def _load_snapshot() -> dict[int, dict[str, Any]]:
    global _snapshot_signature
    global _snapshot_cards
    global _snapshot_standard_dbf_ids

    if not snapshot_enabled():
        return {}
    path = snapshot_path()
    try:
        signature = _file_signature(path)
    except OSError:
        return {}

    with _snapshot_lock:
        if signature == _snapshot_signature:
            return _snapshot_cards
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA_VERSION:
                raise ValueError("unsupported card snapshot schema")
            raw_cards = payload.get("cards")
            if not isinstance(raw_cards, dict):
                raise ValueError("card snapshot cards must be an object")
            loaded = {
                int(dbf_id): dict(card)
                for dbf_id, card in raw_cards.items()
                if isinstance(card, dict)
            }
            raw_standard_ids = payload.get("standard_dbf_ids") or []
            if not isinstance(raw_standard_ids, list):
                raise ValueError("standard dbf ids must be an array")
            loaded_standard_ids = {
                int(dbf_id)
                for dbf_id in raw_standard_ids
                if str(dbf_id).strip().isdigit() and int(dbf_id) > 0
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(
                "[Deckview card snapshot] load fallback: "
                f"{type(exc).__name__}"
            )
            loaded = {}
            loaded_standard_ids = set()
        _snapshot_cards = loaded
        _snapshot_standard_dbf_ids = loaded_standard_ids
        _snapshot_signature = signature
        return _snapshot_cards


def get_snapshot_cards(
    dbf_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Return selected snapshot rows; missing or invalid snapshots are misses."""
    cards = _load_snapshot()
    values: dict[int, dict[str, Any]] = {}
    for raw_dbf_id in dbf_ids:
        try:
            dbf_id = int(raw_dbf_id)
        except (TypeError, ValueError):
            continue
        card = cards.get(dbf_id)
        if card is not None:
            values[dbf_id] = dict(card)
    return values


def get_standard_dbf_ids() -> set[int]:
    """Return the current Standard legality set from the same atomic snapshot."""
    if not snapshot_enabled():
        return set()
    _load_snapshot()
    with _snapshot_lock:
        return set(_snapshot_standard_dbf_ids)


def preload_snapshot() -> int:
    """Load the current catalog before render workers fork; fail open if absent."""
    return len(_load_snapshot()) if snapshot_enabled() else 0


def fetch_standard_dbf_ids(
    *,
    api_root: str,
    api_key: str = "",
    page_size: int = 120,
    timeout: float = 10.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch Arena's current Standard catalog using cursor pagination."""
    client = session or requests.Session()
    page_size = min(120, max(1, int(page_size)))
    cursor: str | None = None
    seen_cursors: set[str] = set()
    dbf_ids: set[int] = set()
    raw_count = 0
    source_total: int | None = None
    revision = ""

    while True:
        params: dict[str, Any] = {
            "format": "standard",
            "limit": page_size,
        }
        if cursor is not None:
            params["cursor"] = cursor
        headers = {
            "Accept": "application/json",
            "User-Agent": "DeckviewBot/1.0",
        }
        if api_key:
            headers["X-API-Key"] = api_key
        response = client.get(
            f"{api_root.rstrip('/')}/cards",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        pagination = (
            payload.get("pagination") if isinstance(payload, dict) else None
        )
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not isinstance(pagination, dict):
            raise ValueError("Arena Standard catalog response is invalid")
        if source_total is None:
            source_total = int(pagination.get("total") or 0)
        if isinstance(meta, dict) and not revision:
            revision = str(meta.get("datasetVersion") or "").strip()
        raw_count += len(rows)
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                dbf_id = int(row.get("dbfId"))
            except (TypeError, ValueError):
                continue
            if dbf_id > 0:
                dbf_ids.add(dbf_id)

        if not pagination.get("hasMore"):
            break
        next_cursor = str(pagination.get("nextCursor") or "").strip()
        if not next_cursor or next_cursor in seen_cursors:
            raise RuntimeError("Arena Standard catalog pagination did not advance")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    if source_total and raw_count < source_total:
        raise ValueError(
            "Arena Standard catalog is incomplete: "
            f"received {raw_count} of {source_total} rows"
        )
    if source_total and not dbf_ids:
        raise ValueError("Arena Standard catalog is unexpectedly empty")
    return {
        "dbf_ids": dbf_ids,
        "revision": revision,
        "source_total": source_total or len(dbf_ids),
    }


def _compact_card(raw: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    try:
        dbf_id = int(raw.get("dbf") if raw.get("dbf") is not None else raw["dbf_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if dbf_id <= 0:
        return None
    card = {field: raw[field] for field in _SNAPSHOT_FIELDS if field in raw}
    return dbf_id, card


def _source_card_priority(raw: dict[str, Any]) -> tuple[int, int, str]:
    """Match the canonical dbf endpoint when multi-class variants share a dbf."""
    card_id = str(raw.get("card_id") or "").strip()
    generated_variant = 1 if re.search(r"_\d+$", card_id) else 0
    try:
        source_id = int(raw.get("id"))
    except (TypeError, ValueError):
        source_id = 2**63 - 1
    return generated_variant, source_id, card_id


def refresh_snapshot(
    *,
    target_path: str | os.PathLike[str] | None = None,
    api_root: str = "https://kolodahs.ru/api/v1",
    locale: str = "ruru",
    page_size: int = 500,
    timeout: float = 10.0,
    session: requests.Session | None = None,
    standard_dbf_ids: Iterable[int] | None = None,
    standard_revision: str = "",
) -> dict[str, Any]:
    """Fetch the public catalog outside the render path and replace atomically."""
    target = Path(target_path) if target_path is not None else snapshot_path()
    if not target.is_absolute():
        target = _PROJECT_ROOT / target
    page_size = min(500, max(1, int(page_size)))
    client = session or requests.Session()
    cards: dict[int, dict[str, Any]] = {}
    priorities: dict[int, tuple[int, int, str]] = {}
    offset = 0
    raw_card_count = 0
    seen_offsets: set[int] = set()
    total: int | None = None

    while True:
        if offset in seen_offsets:
            raise RuntimeError("Kolodahs snapshot pagination loop")
        seen_offsets.add(offset)
        response = client.get(
            f"{api_root.rstrip('/')}/cards",
            params={
                "lang": locale,
                "limit": page_size,
                "offset": offset,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ValueError("Kolodahs cards response has no result")
        raw_cards = result.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("Kolodahs cards response has no cards")
        if total is None:
            total = int(result.get("total") or 0)
        raw_card_count += len(raw_cards)
        for raw in raw_cards:
            if not isinstance(raw, dict):
                continue
            compact = _compact_card(raw)
            if compact is not None:
                dbf_id, card = compact
                priority = _source_card_priority(raw)
                if dbf_id not in priorities or priority < priorities[dbf_id]:
                    cards[dbf_id] = card
                    priorities[dbf_id] = priority

        next_offset = result.get("next_offset")
        if next_offset is None:
            break
        next_offset = int(next_offset)
        if next_offset <= offset:
            raise RuntimeError("Kolodahs snapshot pagination did not advance")
        offset = next_offset

    if total and raw_card_count < total:
        raise ValueError(
            "Kolodahs snapshot is incomplete: "
            f"received {raw_card_count} of {total} source rows"
        )
    if total and not cards:
        raise ValueError("Kolodahs snapshot is unexpectedly empty")

    serialized_cards = {
        str(dbf_id): cards[dbf_id]
        for dbf_id in sorted(cards)
    }
    if standard_dbf_ids is None:
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
            standard_dbf_ids = previous.get("standard_dbf_ids") or []
            if not standard_revision:
                standard_revision = str(
                    previous.get("standard_revision") or ""
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            standard_dbf_ids = []
    serialized_standard_ids = sorted(
        {
            int(dbf_id)
            for dbf_id in standard_dbf_ids
            if str(dbf_id).strip().isdigit() and int(dbf_id) > 0
        }
    )
    canonical = json.dumps(
        {
            "cards": serialized_cards,
            "standard_dbf_ids": serialized_standard_ids,
            "standard_revision": standard_revision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    revision = hashlib.sha256(canonical).hexdigest()
    payload = {
        "schema": _SCHEMA_VERSION,
        "revision": revision,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"{api_root.rstrip('/')}/cards?lang={locale}",
        "source_total": total or len(cards),
        "card_count": len(cards),
        "standard_revision": standard_revision,
        "standard_card_count": len(serialized_standard_ids),
        "standard_dbf_ids": serialized_standard_ids,
        "cards": serialized_cards,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": str(target),
        "revision": revision,
        "card_count": len(cards),
        "source_total": total or len(cards),
        "standard_card_count": len(serialized_standard_ids),
        "size_bytes": len(encoded),
    }


def _reset_snapshot_cache() -> None:
    """Reset process-local state for tests and controlled reloads."""
    global _snapshot_signature
    global _snapshot_cards
    global _snapshot_standard_dbf_ids
    with _snapshot_lock:
        _snapshot_signature = None
        _snapshot_cards = {}
        _snapshot_standard_dbf_ids = set()
