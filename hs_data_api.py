"""
Client and normalizers for https://api.hs-manacost.ru datasets.

The API is backed by github.com/Zulut30/hearthstone-parses and gives us cached
HSGuru/MetaStats/Hearthstone-Decks data without making Deckview fight
Cloudflare on every bot request.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

from config import (
    HS_DATA_API_BASE_URL,
    HS_DATA_API_DECK_INDEX_SOURCES,
    HS_DATA_API_ENABLED,
    HS_DATA_API_META_STANDARD_SOURCE,
    HS_DATA_API_META_WILD_SOURCE,
    HS_DATA_API_STREAMER_SOURCES,
    HS_DATA_API_TIMEOUT,
    HS_DATA_API_USER_AGENT,
)


class HSDataAPIError(RuntimeError):
    pass


_CACHE_TTL = 900
_dataset_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_db_decks_cache: Dict[tuple[str, str, str, str, int], tuple[float, List[Dict[str, Any]]]] = {}
_deck_code_re = re.compile(r"\bAAE[A-Za-z0-9+/]{40,}={0,3}")


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    match = re.search(r"-?\d[\d\s,\xa0]*", text)
    if not match:
        return default
    try:
        return int(re.sub(r"[\s,\xa0]", "", match.group(0)))
    except ValueError:
        return default


def _first_text(row: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _dataset_url(source_id: str) -> str:
    quoted = urllib.parse.quote(source_id.strip(), safe="")
    return f"{HS_DATA_API_BASE_URL}/datasets/{quoted}"


def _api_url(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    url = f"{HS_DATA_API_BASE_URL}/{path.lstrip('/')}"
    if not params:
        return url
    clean_params = {
        key: value
        for key, value in params.items()
        if value is not None and str(value).strip() != ""
    }
    if not clean_params:
        return url
    return f"{url}?{urllib.parse.urlencode(clean_params)}"


def fetch_dataset(source_id: str, *, use_cache: bool = True) -> Dict[str, Any]:
    """Fetch one cached JSON dataset from the public data API."""
    if not HS_DATA_API_ENABLED:
        raise HSDataAPIError("HS Data API is disabled")
    source_id = (source_id or "").strip()
    if not source_id:
        raise HSDataAPIError("empty source_id")

    now = time.monotonic()
    cached = _dataset_cache.get(source_id)
    if use_cache and cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    request = urllib.request.Request(
        _dataset_url(source_id),
        headers={
            "Accept": "application/json",
            "User-Agent": HS_DATA_API_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HS_DATA_API_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        snippet = e.read(240).decode("utf-8", "replace").replace("\n", " ").strip()
        raise HSDataAPIError(f"{source_id} HTTP {e.code}: {snippet}") from e
    except Exception as e:
        raise HSDataAPIError(f"{source_id}: {e}") from e

    if not isinstance(payload, dict):
        raise HSDataAPIError(f"{source_id}: non-object JSON payload")
    _dataset_cache[source_id] = (now, payload)
    return payload


def get_db_decks(
    *,
    q: str = "",
    format_name: str = "",
    class_name: str = "",
    source_id: str = "",
    limit: int = 20,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Search the API's SQL-backed deck table."""
    if not HS_DATA_API_ENABLED:
        raise HSDataAPIError("HS Data API is disabled")

    q = (q or "").strip()
    format_name = (format_name or "").strip()
    class_name = (class_name or "").strip()
    source_id = (source_id or "").strip()
    limit = max(1, min(int(limit or 20), 200))
    cache_key = (q.lower(), format_name.lower(), class_name.lower(), source_id.lower(), limit)

    now = time.monotonic()
    cached = _db_decks_cache.get(cache_key)
    if use_cache and cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    request = urllib.request.Request(
        _api_url(
            "/api/db/decks",
            {
                "q": q,
                "format_name": format_name,
                "class_name": class_name,
                "source_id": source_id,
                "limit": limit,
            },
        ),
        headers={
            "Accept": "application/json",
            "User-Agent": HS_DATA_API_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HS_DATA_API_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        snippet = e.read(240).decode("utf-8", "replace").replace("\n", " ").strip()
        raise HSDataAPIError(f"db decks HTTP {e.code}: {snippet}") from e
    except Exception as e:
        raise HSDataAPIError(f"db decks: {e}") from e

    rows = payload.get("decks") if isinstance(payload, dict) else None
    result = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    _db_decks_cache[cache_key] = (now, result)
    return result


def _structured(dataset: Dict[str, Any]) -> Dict[str, Any]:
    data = dataset.get("data") if isinstance(dataset, dict) else None
    structured = data.get("structured") if isinstance(data, dict) else None
    return structured if isinstance(structured, dict) else {}


def _source_url(dataset: Dict[str, Any]) -> str:
    data = dataset.get("data") if isinstance(dataset, dict) else None
    for container in (data, dataset):
        if not isinstance(container, dict):
            continue
        for key in ("final_url", "fetch_url", "url"):
            value = container.get(key)
            if value:
                return str(value)
    return ""


def _extract_deck_code(*values: Any) -> str:
    for value in values:
        if not value:
            continue
        match = _deck_code_re.search(str(value))
        if match:
            return match.group(0)
    return ""


def _extract_deck_name(deck_blob: str, deck_code: str) -> str:
    text = (deck_blob or "").strip()
    if not text:
        return ""

    name = ""
    if "###" in text:
        tail = text.split("###", 1)[1].strip()
        code_pos = tail.find(deck_code) if deck_code else -1
        if code_pos > 0:
            name = tail[:code_pos].strip()
        elif "#" in tail:
            name = tail.split("#", 1)[0].strip()
        else:
            name = tail.strip()

    if not name:
        cleaned = _deck_code_re.sub(" ", text)
        cleaned = cleaned.replace("###", " ").strip()
        if "#" in cleaned:
            cleaned = cleaned.split("#", 1)[0].strip()
        name = cleaned

    name = re.sub(r"\s+", " ", name).strip(" -#")
    return name[:90] if name else ""


def _parse_win_loss(value: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*[-–]\s*(\d+)", value or "")
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _extract_legend_rank(value: str) -> str:
    match = re.search(r"\d+", (value or "").replace(",", ""))
    return match.group(0) if match else ""


def normalize_streamer_row(
    row: Dict[str, Any],
    *,
    source_id: str,
    dataset: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Normalize an HSGuru streamer-decks API row into Deckview's deck shape."""
    if not isinstance(row, dict):
        return None
    deck_blob = _first_text(row, "Deck", "deck", "title")
    deck_code = _extract_deck_code(deck_blob, row.get("deck_code"), row.get("code"))
    if not deck_code:
        return None

    deck_name_en = _first_text(row, "deck_name", "name", "Archetype", "archetype")
    if not deck_name_en:
        deck_name_en = _extract_deck_name(deck_blob, deck_code)
    if not deck_name_en:
        deck_name_en = "Deck"

    win_loss = _first_text(row, "Win - Loss", "win_loss", "score", "Score")
    wins, losses = _parse_win_loss(win_loss)
    peak = _first_text(row, "Peak", "peak", "rank", "Rank")

    return {
        "deck_code": deck_code,
        "deck_name": deck_name_en,
        "deck_name_en": deck_name_en,
        "streamer": _first_text(row, "Streamer", "streamer", "player", "Player"),
        "format": _first_text(row, "Format", "format"),
        "wins": wins,
        "losses": losses,
        "total_games": wins + losses,
        "peak": peak,
        "latest": _first_text(row, "Latest", "latest"),
        "worst": _first_text(row, "Worst", "worst"),
        "legend_rank": _extract_legend_rank(peak),
        "last_played": _first_text(row, "Last Played", "last_played"),
        "source_id": source_id,
        "source_url": _source_url(dataset),
        "source_fetched_at": str(dataset.get("fetched_at") or ""),
    }


def normalize_hearthstone_decks_row(
    row: Dict[str, Any],
    *,
    source_id: str,
    dataset: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Normalize a hearthstone-decks.net top legend row into Deckview's deck shape."""
    if not isinstance(row, dict):
        return None
    deck_code = _extract_deck_code(row.get("deck_code"), row.get("code"), row.get("Deck"))
    if not deck_code:
        return None

    title = _first_text(row, "title", "Title")
    deck_name = _first_text(row, "archetype", "archetype_name", "deck_name", "name")
    if not deck_name and title:
        deck_name = re.sub(r"\s+#\d+\s+Legend.*$", "", title).strip()
        deck_name = re.sub(r"\s+\(Score:.*?\)\s*$", "", deck_name).strip()
    if not deck_name:
        deck_name = "Deck"

    score = _first_text(row, "score", "Score")
    if not score and title:
        match = re.search(r"Score:\s*([0-9]+\s*[-–]\s*[0-9]+)", title)
        score = match.group(1) if match else ""
    wins, losses = _parse_win_loss(score)
    total = wins + losses
    winrate = f"{wins / total * 100:.1f}%" if total else ""
    rank = _first_text(row, "rank", "Rank")

    return {
        "deck_code": deck_code,
        "deck_name": deck_name,
        "deck_name_en": deck_name,
        "streamer": _first_text(row, "player", "Player"),
        "player": _first_text(row, "player", "Player"),
        "format": _first_text(row, "format", "Format"),
        "wins": wins,
        "losses": losses,
        "total_games": total,
        "winrate": winrate,
        "peak": rank,
        "latest": "",
        "worst": "",
        "legend_rank": _extract_legend_rank(rank),
        "last_played": _first_text(row, "date", "Date"),
        "source_id": source_id,
        "source_site": "hearthstone-decks.net",
        "source_url": _first_text(row, "url", "URL") or _source_url(dataset),
        "source_fetched_at": str(dataset.get("fetched_at") or ""),
    }


def _dedupe_by_code(decks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered: List[str] = []
    by_code: Dict[str, Dict[str, Any]] = {}
    for deck in decks:
        code = (deck.get("deck_code") or "").strip()
        if not code:
            continue
        if code not in by_code:
            ordered.append(code)
            by_code[code] = deck
            continue
        current = by_code[code]
        new_score = int(deck.get("total_games") or 0), int(bool(deck.get("streamer"))), int(bool(deck.get("format")))
        old_score = int(current.get("total_games") or 0), int(bool(current.get("streamer"))), int(bool(current.get("format")))
        if new_score > old_score:
            by_code[code] = deck
    return [by_code[code] for code in ordered]


def get_streamer_decks(source_ids: Optional[Iterable[str]] = None) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return normalized publishable-source decks plus per-source stats."""
    sources = tuple(source_ids or HS_DATA_API_STREAMER_SOURCES)
    decks: List[Dict[str, Any]] = []
    stats: List[Dict[str, Any]] = []
    errors: List[str] = []

    for source_id in sources:
        try:
            dataset = fetch_dataset(source_id)
            structured = _structured(dataset)
            stype = structured.get("type")
            rows = structured.get("rows") or structured.get("decks") or []
            if not isinstance(rows, list):
                rows = []
            if stype == "hearthstone_decks":
                normalizer = normalize_hearthstone_decks_row
            else:
                normalizer = normalize_streamer_row
            parsed = [deck for deck in (normalizer(row, source_id=source_id, dataset=dataset) for row in rows) if deck]
            decks.extend(parsed)
            stats.append({
                "source_id": source_id,
                "type": stype,
                "state": dataset.get("state"),
                "fetched_at": dataset.get("fetched_at"),
                "backend": dataset.get("backend"),
                "rows": len(rows),
                "parsed_decks": len(parsed),
            })
        except Exception as e:
            errors.append(f"{source_id}: {e}")
            stats.append({"source_id": source_id, "error": str(e)[:300]})

    unique = _dedupe_by_code(decks)
    if not unique and errors:
        raise HSDataAPIError("; ".join(errors))
    return unique, stats


def get_meta_strategies(format_id: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source_id = HS_DATA_API_META_STANDARD_SOURCE if int(format_id) == 1 else HS_DATA_API_META_WILD_SOURCE
    dataset = fetch_dataset(source_id)
    structured = _structured(dataset)
    rows = structured.get("strategies") or structured.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    stats = {
        "source_id": source_id,
        "state": dataset.get("state"),
        "fetched_at": dataset.get("fetched_at"),
        "backend": dataset.get("backend"),
        "rows": len(rows),
    }
    return [row for row in rows if isinstance(row, dict)], stats


def _add_index_value(index: Dict[str, str], name: Any, code: str) -> None:
    if not name or not code:
        return
    key = re.sub(r"\s+", " ", str(name).strip().lower())
    if len(key) >= 3 and key not in index:
        index[key] = code


def build_deck_code_index(source_ids: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Build {english archetype/title lower-case: deck_code} from API datasets."""
    sources = tuple(source_ids or HS_DATA_API_DECK_INDEX_SOURCES)
    index: Dict[str, str] = {}
    for source_id in sources:
        try:
            dataset = fetch_dataset(source_id)
            structured = _structured(dataset)
        except Exception as e:
            print(f"[HS Data API] deck index source {source_id} failed: {e}")
            continue

        stype = structured.get("type")
        rows: List[Any] = []
        if stype == "metastats_decks":
            rows = structured.get("decks") or []
        elif stype == "hearthstone_decks":
            rows = structured.get("decks") or []
        elif stype == "streamer_decks":
            rows = structured.get("rows") or []

        for row in rows:
            if not isinstance(row, dict):
                continue
            if stype == "streamer_decks":
                deck_blob = _first_text(row, "Deck", "deck")
                code = _extract_deck_code(deck_blob, row.get("deck_code"), row.get("code"))
                name = _extract_deck_name(deck_blob, code)
                _add_index_value(index, name, code)
                continue

            code = _extract_deck_code(row.get("deck_code"), row.get("code"), row.get("Deck"))
            _add_index_value(index, row.get("archetype_name"), code)
            _add_index_value(index, row.get("archetype"), code)
            title = row.get("title")
            _add_index_value(index, title, code)
            if title:
                _add_index_value(index, re.sub(r"#\d+.*$", "", str(title)).strip(), code)
                _add_index_value(index, re.sub(r"\s+#\d+\s+Legend.*$", "", str(title)).strip(), code)
    return index


def parse_popularity(value: Any) -> tuple[str, int]:
    text = str(value or "").strip()
    popularity = ""
    game_count = 0
    match_pop = re.match(r"([\d.]+%)", text)
    if match_pop:
        popularity = match_pop.group(1)
    match_games = re.search(r"\(([\d\s,\xa0]+)\)", text)
    if match_games:
        game_count = _safe_int(match_games.group(1))
    return popularity, game_count
