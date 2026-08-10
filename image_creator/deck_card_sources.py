"""Authoritative card metadata for deck rendering.

Mana values and stable CardIDs come from the public API of the main
``kolodahs.ru`` site. The caller keeps Blizzard data as a fail-open fallback.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
import urllib3

from deckview.infrastructure.async_tools import to_thread
from framework.hearthstonejson_api import get_loaded_card_by_dbfid
from image_creator.card_catalog_snapshot import get_snapshot_cards


_API_ROOT = os.getenv(
    "KOLODAHS_CARD_API_BASE_URL",
    "https://kolodahs.ru/api/v1",
).rstrip("/")
_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("KOLODAHS_CARD_API_TIMEOUT", "8").replace(",", ".")),
)
_LOCAL_ORIGIN_IP = os.getenv("KOLODAHS_CARD_API_LOCAL_IP", "").strip()
_API_URL = urlparse(_API_ROOT)
_LOCAL_POOL = (
    urllib3.HTTPSConnectionPool(
        _LOCAL_ORIGIN_IP,
        port=_API_URL.port or 443,
        assert_hostname=_API_URL.hostname,
        server_hostname=_API_URL.hostname,
        headers={
            "Host": str(_API_URL.hostname or "kolodahs.ru"),
            "Accept": "application/json",
            "User-Agent": "DeckviewBot/1.0",
        },
        maxsize=max(16, min(64, (os.cpu_count() or 4) * 2)),
        block=True,
    )
    if _LOCAL_ORIGIN_IP and _API_URL.scheme == "https" and _API_URL.hostname
    else None
)
_CACHE_TTL_SECONDS = 24 * 60 * 60
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DISK_CACHE_PATH = Path(
    os.getenv(
        "DECKVIEW_KOLODAHS_CACHE_PATH",
        str(_PROJECT_ROOT / "cache" / "kolodahs_card_metadata.sqlite3"),
    )
)
_thread_local = threading.local()
_cache_lock = threading.RLock()
_card_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}
_existing_slug_index: dict[int, str] | None = None

_RARITY_IDS = {
    "FREE": 1,
    "COMMON": 2,
    "RARE": 3,
    "EPIC": 4,
    "LEGENDARY": 5,
}


def _metadata_with_canonical_identity(
    dbf_id: int,
    source: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Reject a Kolodahs CardID that disagrees with preloaded HSJSON.

    A mismatched CardID can point the downloader at a completely different
    card image.  Use the matching HSJSON record as one coherent identity
    instead of mixing its CardID with the source card's name or text.
    """
    canonical = get_loaded_card_by_dbfid(dbf_id)
    source_card_id = str(source.get("card_id") or "").strip().lstrip("/")
    canonical_card_id = str((canonical or {}).get("cardId") or "").strip()
    if (
        not canonical
        or not source_card_id
        or not canonical_card_id
        or source_card_id == canonical_card_id
    ):
        return source, False

    return (
        {
            **source,
            "card_id": canonical_card_id,
            "name": canonical.get("name"),
            "mana": canonical.get("manaCost"),
            "image_url": canonical.get("image"),
            "collectible": canonical.get("collectible"),
            "rarity": canonical.get("rarity"),
            "type": canonical.get("type"),
        },
        True,
    )


def _remember_main_slug(index: dict[int, str], dbf_id: int, slug: str) -> None:
    """Prefer a main-deck filename over its generated ``-side`` copy."""
    current = index.get(int(dbf_id))
    if current is None or (current.endswith("-side") and not slug.endswith("-side")):
        index[int(dbf_id)] = slug


def _existing_slug(dbf_id: int, card_id: str) -> str:
    """Reuse already downloaded Arena art after switching to local deck parsing."""
    global _existing_slug_index
    with _cache_lock:
        if _existing_slug_index is None:
            index: dict[int, str] = {}
            card_dir = _PROJECT_ROOT / "cards"
            try:
                # Full Hero renders override Arena's portrait-only assets.
                for marker in card_dir.glob("*.hsjson-v1"):
                    slug = marker.name[: -len(".hsjson-v1")]
                    prefix = slug.split("-", 1)[0]
                    if prefix.isdigit():
                        _remember_main_slug(index, int(prefix), slug)
                # Arena markers are authoritative for ordinary cards and much
                # cheaper to scan than opening thousands of PNGs.
                for marker_suffix in (".arena-v2", ".arena-v1"):
                    for marker in card_dir.glob(f"*{marker_suffix}"):
                        slug = marker.name[: -len(marker_suffix)]
                        prefix = slug.split("-", 1)[0]
                        if prefix.isdigit():
                            _remember_main_slug(index, int(prefix), slug)
                for image in card_dir.glob("*.png"):
                    slug = image.stem
                    prefix = slug.split("-", 1)[0]
                    if prefix.isdigit():
                        _remember_main_slug(index, int(prefix), slug)
            except OSError:
                pass
            _existing_slug_index = index
        cached = _existing_slug_index.get(int(dbf_id))
        if cached and not cached.endswith("-side"):
            return cached

    suffix = re.sub(r"[^a-z0-9]+", "-", card_id.lower()).strip("-") or "card"
    return f"{int(dbf_id)}-{suffix}"


def _session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "DeckviewBot/1.0",
            }
        )
        _thread_local.session = session
    return session


def _card_dbf_id(card: dict[str, Any]) -> int | None:
    raw = card.get("dbfId") if card.get("dbfId") is not None else card.get("id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _disk_cache_enabled() -> bool:
    return os.getenv("DECKVIEW_KOLODAHS_DISK_CACHE", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _disk_cache_connection() -> sqlite3.Connection:
    _DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_DISK_CACHE_PATH, timeout=5)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS card_metadata (
            dbf_id INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    return connection


def _load_disk_cache(dbf_ids: list[int]) -> dict[int, dict[str, Any] | None]:
    if not dbf_ids or not _disk_cache_enabled():
        return {}
    try:
        placeholders = ",".join("?" for _ in dbf_ids)
        cutoff = time.time() - _CACHE_TTL_SECONDS
        with _disk_cache_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT dbf_id, payload_json
                FROM card_metadata
                WHERE dbf_id IN ({placeholders}) AND fetched_at >= ?
                """,
                (*dbf_ids, cutoff),
            ).fetchall()
        values: dict[int, dict[str, Any] | None] = {}
        for dbf_id, payload_json in rows:
            payload = json.loads(payload_json)
            values[int(dbf_id)] = dict(payload) if isinstance(payload, dict) else None
        return values
    except Exception as exc:
        print(
            "[Deckview Kolodahs] shared cache read fallback: "
            f"{type(exc).__name__}"
        )
        return {}


def _store_disk_cache(values: dict[int, dict[str, Any] | None]) -> None:
    if not values or not _disk_cache_enabled():
        return
    try:
        fetched_at = time.time()
        rows = [
            (
                int(dbf_id),
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                fetched_at,
            )
            for dbf_id, value in values.items()
        ]
        with _disk_cache_connection() as connection:
            connection.executemany(
                """
                INSERT INTO card_metadata (dbf_id, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(dbf_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    fetched_at=excluded.fetched_at
                """,
                rows,
            )
    except Exception as exc:
        print(
            "[Deckview Kolodahs] shared cache write fallback: "
            f"{type(exc).__name__}"
        )


def get_kolodahs_card(dbf_id: int) -> dict[str, Any] | None:
    """Return a card from kolodahs.ru/cards_ru, or None fail-open."""
    dbf_id = int(dbf_id)
    now = time.monotonic()
    with _cache_lock:
        cached = _card_cache.get(dbf_id)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    try:
        if _LOCAL_POOL is not None:
            base_path = _API_URL.path.rstrip("/")
            response = _LOCAL_POOL.request(
                "GET",
                f"{base_path}/cards/dbf/{quote(str(dbf_id), safe='')}?lang=ruru",
                timeout=urllib3.Timeout(connect=1.0, read=_TIMEOUT_SECONDS),
                retries=False,
            )
            if response.status == 404:
                value = None
            elif response.status >= 400:
                raise RuntimeError(f"Kolodahs local origin HTTP {response.status}")
            else:
                payload = json.loads(response.data)
                raw = payload.get("card") if isinstance(payload, dict) else None
                value = dict(raw) if isinstance(raw, dict) else None
        else:
            response = _session().get(
                f"{_API_ROOT}/cards/dbf/{quote(str(dbf_id), safe='')}",
                params={"lang": "ruru"},
                timeout=_TIMEOUT_SECONDS,
            )
            if response.status_code == 404:
                value = None
            else:
                response.raise_for_status()
                payload = response.json()
                raw = payload.get("card") if isinstance(payload, dict) else None
                value = dict(raw) if isinstance(raw, dict) else None
    except Exception as exc:
        print(
            f"[Deckview Kolodahs] metadata fallback for dbfId={dbf_id}: "
            f"{type(exc).__name__}"
        )
        return None

    with _cache_lock:
        _card_cache[dbf_id] = (time.monotonic(), value)
    return value


def hydrate_deck_cards_sync(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply authoritative Kolodahs metadata to deck card records."""
    dbf_ids = sorted(
        {
            dbf_id
            for card in cards
            if (dbf_id := _card_dbf_id(card)) is not None
        }
    )
    if not dbf_ids:
        return cards

    now = time.monotonic()
    metadata: dict[int, dict[str, Any] | None] = {}
    with _cache_lock:
        for dbf_id in dbf_ids:
            cached = _card_cache.get(dbf_id)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                metadata[dbf_id] = cached[1]

    missing = [dbf_id for dbf_id in dbf_ids if dbf_id not in metadata]
    snapshot_values = get_snapshot_cards(missing)
    if snapshot_values:
        metadata.update(snapshot_values)
        with _cache_lock:
            cached_at = time.monotonic()
            for dbf_id, value in snapshot_values.items():
                _card_cache[dbf_id] = (cached_at, value)

    missing = [dbf_id for dbf_id in dbf_ids if dbf_id not in metadata]
    disk_values = _load_disk_cache(missing) if missing else {}
    if disk_values:
        metadata.update(disk_values)
        with _cache_lock:
            cached_at = time.monotonic()
            for dbf_id, value in disk_values.items():
                _card_cache[dbf_id] = (cached_at, value)

    missing = [dbf_id for dbf_id in dbf_ids if dbf_id not in metadata]
    if missing:
        workers = min(16, os.cpu_count() or 4, len(missing))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            fetched = dict(
                zip(missing, executor.map(get_kolodahs_card, missing))
            )
        metadata.update(fetched)
        # Network failures are deliberately not written by get_kolodahs_card.
        # Persist only authoritative API results (including a real HTTP 404).
        with _cache_lock:
            persistable = {
                dbf_id: _card_cache[dbf_id][1]
                for dbf_id in missing
                if dbf_id in _card_cache
            }
        _store_disk_cache(persistable)

    for card in cards:
        dbf_id = _card_dbf_id(card)
        source = metadata.get(dbf_id) if dbf_id is not None else None
        if not source:
            continue
        source, identity_corrected = _metadata_with_canonical_identity(
            dbf_id,
            source,
        )
        if identity_corrected:
            card["deckviewMetadataFallback"] = "hsjson-card-id-mismatch"
        mana_cost = source.get("mana")
        if mana_cost is not None:
            try:
                card["manaCost"] = int(mana_cost)
                card["deckviewManaSource"] = "kolodahs"
            except (TypeError, ValueError):
                pass
        # A few legacy rows on kolodahs.ru contain an accidental leading slash.
        # Arena expects the canonical CardID (for example BAR_801).
        card_id = str(source.get("card_id") or "").strip().lstrip("/")
        if card_id:
            card["cardId"] = card_id
            card["deckviewImageSource"] = "arena"
            if not str(card.get("slug") or "").strip():
                card["slug"] = _existing_slug(dbf_id, card_id)

        name = str(source.get("name") or "").strip()
        if name and not str(card.get("name") or "").strip():
            card["name"] = name
        image_url = str(source.get("image_url") or "").strip()
        if image_url and not str(card.get("image") or "").strip():
            card["image"] = image_url
        if source.get("collectible") is not None:
            card["collectible"] = bool(source.get("collectible"))
        rarity = str(source.get("rarity") or "").strip().upper()
        if rarity and card.get("rarityId") is None:
            card["rarityId"] = _RARITY_IDS.get(rarity)
        player_class = str(source.get("player_class") or "").strip().upper()
        if player_class:
            card["deckviewPlayerClass"] = player_class
        card_type = str(source.get("type") or "").strip().upper()
        if card_type:
            card["deckviewCardType"] = card_type
        card.setdefault("dbfId", dbf_id)
    return cards


@to_thread
def hydrate_deck_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return hydrate_deck_cards_sync(cards)
