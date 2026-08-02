"""Cached client for the official arena.hs-manacost.ru Public API."""

from __future__ import annotations

import hashlib
import base64
import difflib
import re
import threading
import time
import unicodedata
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote, urlparse

import requests
import os

from config import (
    MANACOST_PUBLIC_API_BASE_URL,
    MANACOST_PUBLIC_API_KEY,
    MANACOST_PUBLIC_API_TIMEOUT,
)
_API_ORIGIN_URL = os.getenv(
    "MANACOST_PUBLIC_API_ORIGIN_URL",
    MANACOST_PUBLIC_API_BASE_URL,
).strip().rstrip("/")
_API_ROOT = f"{_API_ORIGIN_URL}/api/v1"
_CACHE_TTL_SECONDS = 15 * 60
_IMAGE_CACHE_TTL_SECONDS = 24 * 60 * 60
_thread_local = threading.local()
_lock = threading.RLock()
_cache: dict[str, tuple[float, Any]] = {}
_cache_inflight: dict[str, threading.Event] = {}
_cache_errors: dict[str, tuple[float, str]] = {}
_CACHE_ERROR_TTL_SECONDS = 3.0
_deck_lookup: dict[str, dict] = {}
_search_lookup: dict[str, tuple[float, list[dict]]] = {}


class ManacostAPIError(RuntimeError):
    pass


def _http_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
    raise ValueError("Truncated varint")


def _decode_deckstring(deck_code: str) -> set[int]:
    """Return dbfIds from a Hearthstone deckstring without external API calls."""
    raw = base64.b64decode(deck_code)
    offset = 0
    _reserved, offset = _read_varint(raw, offset)
    _version, offset = _read_varint(raw, offset)
    _format, offset = _read_varint(raw, offset)
    hero_count, offset = _read_varint(raw, offset)
    for _ in range(hero_count):
        _hero, offset = _read_varint(raw, offset)
    result: set[int] = set()
    for copies in (1, 2):
        count, offset = _read_varint(raw, offset)
        for _ in range(count):
            dbf_id, offset = _read_varint(raw, offset)
            result.add(dbf_id)
    if offset < len(raw):
        count, offset = _read_varint(raw, offset)
        for _ in range(count):
            dbf_id, offset = _read_varint(raw, offset)
            _copies, offset = _read_varint(raw, offset)
            result.add(dbf_id)
    return result


def _headers() -> dict[str, str]:
    if not MANACOST_PUBLIC_API_KEY:
        raise ManacostAPIError("MANACOST_PUBLIC_API_KEY не настроен")
    return {
        "X-API-Key": MANACOST_PUBLIC_API_KEY,
        "Accept": "application/json",
        "User-Agent": "DeckviewBot/1.0",
    }


def _get_json(path: str, params: dict | None = None) -> dict:
    response = _http_session().get(
        f"{_API_ROOT}{path}",
        headers=_headers(),
        params=params,
        timeout=MANACOST_PUBLIC_API_TIMEOUT,
    )
    if response.status_code >= 400:
        message = ""
        try:
            message = str((response.json().get("error") or {}).get("message") or "")
        except Exception:
            pass
        raise ManacostAPIError(
            f"API вернул HTTP {response.status_code}"
            + (f": {message}" if message else "")
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ManacostAPIError("API вернул ответ неверного формата")
    return payload


def _cached(key: str, ttl: float, loader):
    """Cache with per-key singleflight to prevent upstream request stampedes."""
    while True:
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            recent_error = _cache_errors.get(key)
            if (
                recent_error
                and now - recent_error[0] < _CACHE_ERROR_TTL_SECONDS
            ):
                raise ManacostAPIError(recent_error[1])
            pending = _cache_inflight.get(key)
            if pending is None:
                pending = threading.Event()
                _cache_inflight[key] = pending
                is_loader = True
            else:
                is_loader = False

        if is_loader:
            break
        # A loader can fetch several paginated API responses. Waiting callers
        # consume no extra connection and retry the cache after it completes.
        pending.wait(
            timeout=max(30.0, float(MANACOST_PUBLIC_API_TIMEOUT) * 4)
        )

    try:
        value = loader()
    except BaseException as exc:
        with _lock:
            _cache_errors[key] = (
                time.monotonic(),
                str(exc)[:500] or type(exc).__name__,
            )
            event = _cache_inflight.pop(key, None)
            if event is not None:
                event.set()
        raise
    with _lock:
        _cache[key] = (time.monotonic(), value)
        _cache_errors.pop(key, None)
        event = _cache_inflight.pop(key, None)
        if event is not None:
            event.set()
    return value


def search_cards(query: str, *, card_format: str = "wild", limit: int = 15) -> list[dict]:
    payload = _get_json(
        "/cards",
        {
            "format": card_format,
            "query": str(query or "").strip()[:120],
            "limit": max(1, min(int(limit), 120)),
        },
    )
    return list(payload.get("data") or [])


def _local_card_to_public_shape(candidate: dict) -> dict:
    """Normalize HSJSON metadata to the Public API card shape."""
    card_id = str(candidate.get("cardId") or "").strip()
    try:
        dbf_id = int(candidate.get("id"))
    except (TypeError, ValueError) as exc:
        raise ManacostAPIError("У локальной карты отсутствует dbfId") from exc
    if not card_id:
        raise ManacostAPIError("У локальной карты отсутствует CardID")
    card_type = str(candidate.get("type") or "").upper()
    mechanics = []
    for mechanic in candidate.get("mechanics") or []:
        if isinstance(mechanic, dict):
            mechanic = mechanic.get("name") or mechanic.get("id")
        value = str(mechanic or "").strip()
        if value:
            mechanics.append(value)
    return {
        "id": card_id,
        "dbfId": dbf_id,
        "slug": str(candidate.get("slug") or f"{dbf_id}-{card_id}"),
        "collectible": bool(candidate.get("collectible")),
        "formats": list(candidate.get("formats") or []),
        "name": {"ru": str(candidate.get("name") or card_id), "en": ""},
        "text": {"ru": str(candidate.get("text") or ""), "en": ""},
        "flavor": {"ru": str(candidate.get("flavor") or ""), "en": ""},
        "set": str(candidate.get("set") or ""),
        "type": {"id": card_type, "nameRu": ""},
        "rarity": str(candidate.get("rarity") or ""),
        "cardClass": str(candidate.get("cardClass") or ""),
        "multiClass": list(candidate.get("multiClass") or []),
        "minionType": candidate.get("minionType"),
        "minionTypes": list(candidate.get("minionTypes") or []),
        "spellSchool": candidate.get("spellSchool"),
        "cost": candidate.get("manaCost", candidate.get("cost")),
        "attack": candidate.get("attack"),
        "health": candidate.get("health"),
        "durability": candidate.get("durability"),
        "armor": candidate.get("armor"),
        "artist": str(candidate.get("artist") or ""),
        "mechanics": mechanics,
        "referencedTags": list(candidate.get("referencedTags") or []),
        "keywordIds": list(candidate.get("keywordIds") or []),
        "images": {
            variant: f"/api/v1/cards/{card_id}/images/{variant}.webp"
            for variant in ("thumb", "full", "tile")
        },
        "_metadataFallback": "hearthstonejson",
    }


def _card_image_available(card_id: str) -> bool:
    """Cheaply verify that Manacost can serve the card before exposing it."""
    try:
        response = _http_session().head(
            (
                f"{_API_ROOT}/cards/"
                f"{quote(str(card_id), safe='_')}/images/full.webp"
            ),
            headers={**_headers(), "Accept": "image/webp,image/*"},
            timeout=MANACOST_PUBLIC_API_TIMEOUT,
        )
        content_type = str(response.headers.get("Content-Type") or "").lower()
        return response.status_code == 200 and content_type.startswith("image/")
    except Exception:
        return False


def get_card_with_fallback(
    dbf_id: int,
    local_card: dict | None = None,
    *,
    card_format: str = "wild",
) -> dict:
    """Resolve metadata while allowing non-collectible Manacost image records."""
    candidate = local_card or {}
    card_id = str(candidate.get("cardId") or "").strip()
    if not card_id and isinstance(candidate.get("id"), str):
        card_id = str(candidate["id"]).strip()
    if card_id:
        try:
            return get_card(card_id, card_format=card_format)
        except ManacostAPIError:
            pass
    if not candidate or not candidate.get("cardId"):
        try:
            from framework.hearthstonejson_api import get_card_by_dbfid

            candidate = get_card_by_dbfid(int(dbf_id)) or {}
            card_id = str(candidate.get("cardId") or "").strip()
        except Exception:
            candidate = {}
            card_id = ""
    if card_id and _card_image_available(card_id):
        return _local_card_to_public_shape(candidate)
    raise ManacostAPIError("Карта не найдена в базе данных Манакоста")


_LATIN_LOOKALIKES = str.maketrans(
    {
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
        "k": "к",
        "m": "м",
        "t": "т",
        "b": "в",
        "h": "н",
    }
)
_CYRILLIC_LOOKALIKES = str.maketrans(
    {
        "а": "a",
        "с": "c",
        "е": "e",
        "о": "o",
        "р": "p",
        "х": "x",
        "у": "y",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "b",
        "н": "h",
    }
)
_EN_KEYBOARD = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"
_RU_KEYBOARD = "йцукенгшщзхъфывапролджэячсмитьбю."
_EN_TO_RU_KEYBOARD = str.maketrans(_EN_KEYBOARD, _RU_KEYBOARD)
_RU_TO_EN_KEYBOARD = str.maketrans(_RU_KEYBOARD, _EN_KEYBOARD)


def _normalize_card_search_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _card_query_variants(query: str) -> list[str]:
    """Generate conservative typo, mixed-alphabet and keyboard-layout variants."""
    raw = " ".join(str(query or "").strip().split())
    normalized = _normalize_card_search_text(raw)
    variants: list[str] = []

    def add(value: str) -> None:
        value = " ".join(str(value or "").strip().split())
        if value and value.casefold() not in {item.casefold() for item in variants}:
            variants.append(value)

    add(raw)
    add(normalized)
    latin_count = len(re.findall(r"[a-z]", normalized))
    cyrillic_count = len(re.findall(r"[а-я]", normalized))
    if cyrillic_count >= latin_count and latin_count:
        add(normalized.translate(_LATIN_LOOKALIKES))
    elif latin_count > cyrillic_count and cyrillic_count:
        add(normalized.translate(_CYRILLIC_LOOKALIKES))
    if latin_count and not cyrillic_count:
        add(normalized.translate(_EN_TO_RU_KEYBOARD))
    elif cyrillic_count and not latin_count:
        add(normalized.translate(_RU_TO_EN_KEYBOARD))
    return variants


def _card_names(card: dict) -> list[str]:
    value = card.get("name")
    if isinstance(value, dict):
        values = (value.get("ru"), value.get("en"))
    else:
        values = (value,)
    return [
        normalized
        for normalized in (
            _normalize_card_search_text(item)
            for item in values
        )
        if normalized
    ]


def _card_search_score(card: dict, variants: list[str]) -> tuple:
    queries = [
        normalized
        for normalized in (
            _normalize_card_search_text(value)
            for value in variants
        )
        if normalized
    ]
    names = _card_names(card)
    identifiers = {
        _normalize_card_search_text(card.get("id")),
        _normalize_card_search_text(card.get("dbfId")),
    }
    best = (5, 999, 1, "")
    for query in queries:
        if query in identifiers:
            best = min(best, (0, 0, 0, query))
        for name in names:
            if query == name:
                category = 0
                distance = 0
            elif query in name.split():
                category = 1
                distance = len(name) - len(query)
            elif name.startswith(query):
                category = 2
                distance = len(name) - len(query)
            elif query in name:
                category = 3
                distance = len(name) - len(query)
            else:
                category = 4
                distance = round(
                    100
                    * (
                        1
                        - difflib.SequenceMatcher(
                            None,
                            query,
                            name,
                        ).ratio()
                    )
                )
            standard_penalty = (
                0
                if "standard"
                in {
                    str(value or "").lower()
                    for value in (card.get("formats") or [])
                }
                else 1
            )
            best = min(
                best,
                (category, distance, standard_penalty, name),
            )
    return best


def search_cards_flexible(
    query: str,
    *,
    card_format: str = "wild",
    limit: int = 15,
) -> list[dict]:
    """Search displayable Manacost cards while tolerating common user input."""
    variants = _card_query_variants(query)
    if not variants:
        return []
    requested_limit = max(1, min(int(limit), 30))
    api_limit = max(30, requested_limit * 3)
    matches: dict[str, dict] = {}
    first_error: Exception | None = None

    # Stop after the first useful spelling variant. This avoids polluting a
    # successful English search with its accidental Russian keyboard mapping.
    for variant in variants:
        try:
            rows = search_cards(
                variant,
                card_format=card_format,
                limit=api_limit,
            )
        except ManacostAPIError as exc:
            first_error = first_error or exc
            continue
        for card in rows:
            card_id = str(card.get("id") or "").strip()
            if card_id:
                matches[card_id] = card
        if rows:
            break

    ranked = sorted(
        matches.values(),
        key=lambda card: _card_search_score(card, variants),
    )
    best_category = (
        _card_search_score(ranked[0], variants)[0]
        if ranked
        else 5
    )

    # HSJSON repairs typos and supplies stable CardIDs missing from constructed
    # search. A candidate is exposed only when Manacost confirms that its image
    # endpoint can serve the card.
    if not ranked or best_category >= 4:
        try:
            from framework.hearthstonejson_api import (
                find_cards_by_query,
                search_cards_fuzzy,
                suggest_cards_by_name,
            )

            local_candidates: list[dict] = []
            seen_dbf: set[int] = set()
            for variant in variants:
                candidates = (
                    find_cards_by_query(variant)
                    or search_cards_fuzzy(variant, limit=12)
                    or suggest_cards_by_name(variant, limit=8)
                )
                for candidate in candidates:
                    try:
                        dbf_id = int(candidate.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if dbf_id not in seen_dbf:
                        seen_dbf.add(dbf_id)
                        local_candidates.append(candidate)
                    if len(local_candidates) >= 18:
                        break
                if local_candidates:
                    break

            def resolve(candidate: dict) -> dict | None:
                try:
                    dbf_id = int(candidate.get("id"))
                except (TypeError, ValueError):
                    return None
                try:
                    return get_card_with_fallback(
                        dbf_id,
                        candidate,
                        card_format=card_format,
                    )
                except ManacostAPIError:
                    return None

            with ThreadPoolExecutor(max_workers=6) as executor:
                for card in executor.map(resolve, local_candidates):
                    if card and card.get("id"):
                        matches[str(card["id"])] = card
        except Exception:
            pass

    if not matches and first_error is not None:
        raise first_error

    ranked = sorted(
        matches.values(),
        key=lambda card: _card_search_score(card, variants),
    )
    exact = [
        card
        for card in ranked
        if _card_search_score(card, variants)[0] == 0
    ]
    if exact:
        ranked = exact
    elif ranked and _card_search_score(ranked[0], variants)[0] == 1:
        ranked = [
            card
            for card in ranked
            if _card_search_score(card, variants)[0] == 1
        ]
    elif ranked and _card_search_score(ranked[0], variants)[0] == 4:
        ranked = [
            card
            for card in ranked
            if _card_search_score(card, variants)[1] <= 42
        ]
    return ranked[:requested_limit]


def get_card(card_id: str, *, card_format: str = "wild") -> dict:
    key = f"card:{card_format}:{card_id}"

    def load():
        payload = _get_json(
            f"/cards/{quote(str(card_id), safe='_')}",
            {"format": card_format},
        )
        return dict(payload.get("data") or {})

    return _cached(key, _CACHE_TTL_SECONDS, load)


def get_card_by_dbf_id(dbf_id: int, *, card_format: str = "wild") -> dict:
    matches = search_cards(str(int(dbf_id)), card_format=card_format, limit=5)
    for card in matches:
        if int(card.get("dbfId") or 0) == int(dbf_id):
            return get_card(str(card["id"]), card_format=card_format)
    # Numeric full-text search can match a card id suffix before the dbfId.
    # HSJSON gives us the stable CardID, which the Public API accepts directly.
    try:
        from framework.hearthstonejson_api import get_card_by_dbfid

        local_card = get_card_by_dbfid(int(dbf_id))
        card_id = str((local_card or {}).get("cardId") or "").strip()
        if card_id:
            return get_card(card_id, card_format=card_format)
    except Exception:
        pass
    raise ManacostAPIError("Карта не найдена в Public API")


def get_card_image(card_id: str, variant: str = "full") -> bytes:
    if variant not in {"thumb", "full", "tile"}:
        raise ValueError("Неизвестный вариант изображения")
    key = f"image:{card_id}:{variant}"

    def load():
        response = _http_session().get(
            f"{_API_ROOT}/cards/{quote(str(card_id), safe='_')}/images/{variant}.webp",
            headers={**_headers(), "Accept": "image/webp,image/*"},
            timeout=MANACOST_PUBLIC_API_TIMEOUT,
        )
        if response.status_code >= 400 or not response.content:
            raise ManacostAPIError(f"Изображение карты: HTTP {response.status_code}")
        return bytes(response.content)

    return _cached(key, _IMAGE_CACHE_TTL_SECONDS, load)


def get_card_full_art(card_id: str) -> bytes:
    """Fetch the original vertical full art exposed by the Arena card page."""
    normalized_id = str(card_id or "").strip()
    key = f"full-art:{normalized_id}"

    def load():
        detail_response = _http_session().get(
            (
                f"{_API_ORIGIN_URL}"
                f"/api/constructed-cards/{quote(normalized_id, safe='_')}"
            ),
            headers={
                "Accept": "application/json",
                "User-Agent": "DeckviewBot/1.0",
            },
            timeout=MANACOST_PUBLIC_API_TIMEOUT,
        )
        if detail_response.status_code == 404:
            art_url = (
                "https://art.hearthstonejson.com/v1/512x/"
                f"{quote(normalized_id, safe='_')}.jpg"
            )
        elif detail_response.status_code >= 400:
            raise ManacostAPIError(
                f"Full art metadata: HTTP {detail_response.status_code}"
            )
        else:
            payload = detail_response.json()
            card = dict(payload.get("card") or {})
            images = dict(card.get("images") or {})
            art_url = str(images.get("art") or "").strip()
            if not art_url:
                gallery = list((card.get("wiki") or {}).get("gallery") or [])
                full_art = next(
                    (
                        item
                        for item in gallery
                        if "full art" in str(item.get("caption") or "").lower()
                        or "_full." in str(item.get("file_title") or "").lower()
                    ),
                    None,
                )
                art_url = str((full_art or {}).get("file_url") or "").strip()
        parsed = urlparse(art_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "hearthstone.wiki.gg",
                "arena.hs-manacost.ru",
                "d15f34w2p8l1cc.cloudfront.net",
                "art.hearthstonejson.com",
            }
        ):
            raise ManacostAPIError("Полный арт для этой карты недоступен")

        art_response = _http_session().get(
            art_url,
            headers={
                "Accept": "image/jpeg,image/png,image/webp,image/*",
                "User-Agent": "DeckviewBot/1.0",
            },
            timeout=MANACOST_PUBLIC_API_TIMEOUT,
        )
        content_type = str(art_response.headers.get("Content-Type") or "").lower()
        if (
            art_response.status_code >= 400
            or not art_response.content
            or not content_type.startswith("image/")
            or len(art_response.content) > 15 * 1024 * 1024
        ):
            raise ManacostAPIError(
                f"Полный арт: HTTP {art_response.status_code}"
            )
        return bytes(art_response.content)

    return _cached(key, _IMAGE_CACHE_TTL_SECONDS, load)


def get_card_bundle(card_id: str, *, card_format: str = "wild") -> tuple[dict, bytes]:
    """Fetch card metadata and its full image concurrently."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        card_future = executor.submit(get_card, card_id, card_format=card_format)
        image_future = executor.submit(get_card_image, card_id, "full")
        return card_future.result(), image_future.result()


def get_card_bundle_with_fallback(
    dbf_id: int,
    local_card: dict | None = None,
    *,
    card_format: str = "wild",
) -> tuple[dict, bytes]:
    """Fetch metadata with fallback and the Manacost card render concurrently."""
    candidate = local_card or {}
    card_id = str(candidate.get("cardId") or "").strip()
    if not card_id and isinstance(candidate.get("id"), str):
        card_id = str(candidate["id"]).strip()
    if not card_id:
        try:
            from framework.hearthstonejson_api import get_card_by_dbfid

            candidate = get_card_by_dbfid(int(dbf_id)) or {}
            card_id = str(candidate.get("cardId") or "").strip()
        except Exception:
            card_id = ""
    if not card_id:
        raise ManacostAPIError("У карты отсутствует CardID")
    with ThreadPoolExecutor(max_workers=2) as executor:
        card_future = executor.submit(
            get_card_with_fallback,
            dbf_id,
            candidate,
            card_format=card_format,
        )
        image_future = executor.submit(get_card_image, card_id, "full")
        return card_future.result(), image_future.result()


def card_web_url(card: dict) -> str:
    """Return the site's direct constructed-card detail route."""
    card_id = str(card.get("id") or card.get("dbfId") or "").strip()
    formats = {
        str(value or "").strip().lower()
        for value in (card.get("formats") or [])
    }
    card_format = "standard" if "standard" in formats else "wild"
    return (
        f"{MANACOST_PUBLIC_API_BASE_URL.rstrip('/')}/standard/cards/"
        f"{card_format}/{quote(card_id, safe='_')}/"
    )


def _format_key(format_id_or_name: int | str) -> str:
    if str(format_id_or_name).lower() in {"2", "wild", "вольный"}:
        return "wild"
    return "standard"


def get_meta(format_id_or_name: int | str, *, limit: int = 12) -> dict:
    card_format = _format_key(format_id_or_name)
    key = f"meta:{card_format}:{limit}"

    def load():
        payload = _get_json(
            "/meta-statistics",
            {
                "format": card_format,
                "rank": "legend",
                "period": "patch",
                "minGames": 100,
                "limit": max(1, min(int(limit), 500)),
            },
        )
        return {
            "format": card_format,
            "items": list(payload.get("data") or []),
            "meta": dict(payload.get("meta") or {}),
        }

    return _cached(key, _CACHE_TTL_SECONDS, load)


def _register_decks(decks: Iterable[dict]) -> None:
    with _lock:
        for deck in decks:
            deck_id = str(deck.get("deckId") or "").strip()
            if deck_id:
                _deck_lookup[deck_id] = deck


def get_decks(
    format_id_or_name: int | str,
    *,
    all_pages: bool = False,
    min_games: int = 10,
) -> dict:
    card_format = _format_key(format_id_or_name)
    key = f"decks:{card_format}:{int(all_pages)}:{min_games}"
    if not all_pages:
        full_key = f"decks:{card_format}:1:{min_games}"
        now = time.monotonic()
        with _lock:
            full_hit = _cache.get(full_key)
            if full_hit and now - full_hit[0] < _CACHE_TTL_SECONDS:
                return full_hit[1]

    def load():
        rows: list[dict] = []
        cursor = None
        meta: dict = {}
        while True:
            params: dict[str, Any] = {
                "format": card_format,
                "minGames": max(0, int(min_games)),
                "limit": 500,
            }
            if cursor:
                params["cursor"] = cursor
            payload = _get_json("/deck-statistics", params)
            rows.extend(payload.get("data") or [])
            meta = dict(payload.get("meta") or meta)
            pagination = payload.get("pagination") or {}
            if not all_pages or not pagination.get("hasMore"):
                break
            cursor = pagination.get("nextCursor")
            if not cursor or len(rows) >= 10_000:
                break
        _register_decks(rows)
        return {"format": card_format, "items": rows, "meta": meta}

    return _cached(key, _CACHE_TTL_SECONDS, load)


def get_deck(deck_id: str) -> dict | None:
    with _lock:
        deck = _deck_lookup.get(str(deck_id))
    if deck:
        return deck
    for card_format in ("standard", "wild"):
        get_decks(card_format, all_pages=False)
        with _lock:
            deck = _deck_lookup.get(str(deck_id))
        if deck:
            return deck
    return None


def best_decks_by_archetype(
    format_id_or_name: int | str,
    archetype_slugs: Iterable[str] | None = None,
) -> tuple[dict[str, dict], dict]:
    payload = get_decks(format_id_or_name, all_pages=False)
    best: dict[str, dict] = {}
    for deck in payload["items"]:
        slug = str((deck.get("archetype") or {}).get("slug") or "")
        games = int((deck.get("metrics") or {}).get("games") or 0)
        if slug and games > int((best.get(slug, {}).get("metrics") or {}).get("games") or -1):
            best[slug] = deck
    card_format = _format_key(format_id_or_name)
    missing = [
        str(slug)
        for slug in (archetype_slugs or [])
        if slug and str(slug) not in best
    ]

    def load_one(slug: str) -> tuple[str, dict | None]:
        def load():
            response = _get_json(
                "/deck-statistics",
                {
                    "format": card_format,
                    "archetype": slug,
                    "minGames": 0,
                    "limit": 1,
                },
            )
            rows = list(response.get("data") or [])
            _register_decks(rows)
            return rows[0] if rows else None

        return slug, _cached(f"archetype-deck:{card_format}:{slug}", _CACHE_TTL_SECONDS, load)

    if missing:
        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as executor:
            for slug, deck in executor.map(load_one, missing):
                if deck:
                    best[slug] = deck
    return best, payload["meta"]


def find_decks_with_cards(
    dbf_ids: Iterable[int],
    *,
    formats: Iterable[str] = ("standard", "wild"),
) -> list[dict]:
    wanted = {int(value) for value in dbf_ids}
    if not wanted:
        return []
    found: list[dict] = []
    seen_codes: set[str] = set()
    format_list = list(formats)
    with ThreadPoolExecutor(max_workers=min(2, len(format_list) or 1)) as executor:
        payloads = list(
            executor.map(
                lambda card_format: get_decks(card_format, all_pages=True),
                format_list,
            )
        )
    for card_format, payload in zip(format_list, payloads):
        for raw in payload["items"]:
            code = str(raw.get("deckCode") or "").strip()
            if not code or code in seen_codes:
                continue
            try:
                card_ids = _decode_deckstring(code)
            except Exception:
                continue
            if not wanted.issubset(card_ids):
                continue
            seen_codes.add(code)
            archetype = raw.get("archetype") or {}
            metrics = raw.get("metrics") or {}
            found.append(
                {
                    "id": raw.get("deckId"),
                    "deck_id": raw.get("deckId"),
                    "deck_code": code,
                    "deck_name": archetype.get("localizedName") or archetype.get("name") or "Колода",
                    "archetype_name": archetype.get("localizedName") or archetype.get("name"),
                    "format": raw.get("format") or card_format,
                    "games": int(metrics.get("games") or 0),
                    "winrate": metrics.get("winratePercent"),
                    "links": dict(raw.get("links") or {}),
                }
            )
    found.sort(key=lambda item: (item["games"], item.get("winrate") or 0), reverse=True)
    return found


def remember_search(decks: list[dict]) -> str:
    identity = "\n".join(str(deck.get("deck_id") or "") for deck in decks)
    token = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    with _lock:
        _search_lookup[token] = (time.monotonic(), list(decks))
    return token


def remembered_search(token: str) -> list[dict] | None:
    with _lock:
        hit = _search_lookup.get(str(token))
    if not hit or time.monotonic() - hit[0] > _CACHE_TTL_SECONDS:
        return None
    return list(hit[1])


def api_deck_to_bot(deck: dict) -> dict:
    archetype = deck.get("archetype") or {}
    metrics = deck.get("metrics") or {}
    return {
        "id": None,
        "deck_code": deck.get("deckCode"),
        "deck_name": archetype.get("localizedName") or archetype.get("name") or "Колода",
        "archetype_name": archetype.get("localizedName") or archetype.get("name"),
        "deck_mode": "Вольный" if deck.get("format") == "wild" else "Стандарт",
        "games": int(metrics.get("games") or 0),
        "winrate": metrics.get("winratePercent"),
        "links": dict(deck.get("links") or {}),
    }
