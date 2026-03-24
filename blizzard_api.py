"""
Blizzard Hearthstone API client with caching.
Loads cards via OAuth and stores results locally to avoid frequent API calls.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_TIMEOUT = 30
MAX_PAGES_GUARD = 200


def _request_json(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
                  data: Optional[bytes] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict:
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _build_token_url(region: str) -> str:
    return "https://oauth.battle.net/token"


def _build_api_host(region: str) -> str:
    return f"https://{region}.api.blizzard.com"


def get_access_token(client_id: str, client_secret: str, region: str) -> str:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "ManacostBot/1.0",
    }
    token_url = _build_token_url(region)
    payload = _request_json(token_url, method="POST", headers=headers, data=data)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Blizzard OAuth: access_token not found in response")
    return token


def fetch_metadata(region: str, locale: str, token: str) -> Dict[str, Dict[int, str]]:
    host = _build_api_host(region)
    namespace = f"static-{region}"
    params = urllib.parse.urlencode({
        "locale": locale,
        "namespace": namespace,
    })
    url = f"{host}/hearthstone/metadata?{params}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "ManacostBot/1.0",
    }
    data = _request_json(url, headers=headers)
    classes = {item.get("id"): (item.get("slug") or "").upper() for item in data.get("classes", [])}
    rarities = {item.get("id"): (item.get("slug") or "").upper() for item in data.get("rarities", [])}
    types = {item.get("id"): (item.get("slug") or "").upper() for item in data.get("types", [])}
    return {"classes": classes, "rarities": rarities, "types": types}


def fetch_cards_page(region: str, locale: str, token: str, page: int, page_size: int,
                     collectible: Optional[int] = None) -> Dict:
    host = _build_api_host(region)
    namespace = f"static-{region}"
    params_dict = {
        "locale": locale,
        "namespace": namespace,
        "page": page,
        "pageSize": page_size,
    }
    if collectible is not None:
        params_dict["collectible"] = collectible
    params = urllib.parse.urlencode(params_dict)
    url = f"{host}/hearthstone/cards?{params}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "ManacostBot/1.0",
    }
    return _request_json(url, headers=headers)


def _cache_path(cache_dir: Path, locale: str) -> Path:
    safe_locale = locale.replace("/", "_")
    return cache_dir / f"blizzard_cards_{safe_locale}.json"


def _is_cache_valid(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at", 0))
    except Exception:
        return False
    return (time.time() - fetched_at) <= ttl_seconds


def _read_cache(path: Path) -> Tuple[List[Dict], Optional[Dict]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("cards", []), payload.get("metadata")


def _write_cache(path: Path, cards: List[Dict], metadata: Optional[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.time(),
        "cards": cards,
        "metadata": metadata,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_cards_from_blizzard(
    client_id: str,
    client_secret: str,
    region: str,
    locale: str,
    cache_dir: Path,
    cache_ttl_hours: int = 24,
    include_metadata: bool = True,
    page_size: int = 500,
    collectible_only: bool = False,
) -> Tuple[List[Dict], Optional[Dict]]:
    ttl_seconds = max(0, int(cache_ttl_hours) * 3600)
    cache_path = _cache_path(cache_dir, locale)
    if ttl_seconds > 0 and _is_cache_valid(cache_path, ttl_seconds):
        return _read_cache(cache_path)

    token = get_access_token(client_id, client_secret, region)
    metadata = fetch_metadata(region, locale, token) if include_metadata else None

    def _fetch_all_pages(collectible_value: Optional[int]) -> List[Dict]:
        page = 1
        page_count = 1
        results: List[Dict] = []
        while page <= page_count:
            data = fetch_cards_page(
                region,
                locale,
                token,
                page=page,
                page_size=page_size,
                collectible=collectible_value,
            )
            if not isinstance(data, dict):
                break
            page_count = int(data.get("pageCount", page_count) or page_count)
            results.extend(data.get("cards", []))
            page += 1
            if page > MAX_PAGES_GUARD:
                raise RuntimeError("Blizzard API page guard triggered (too many pages)")
        return results

    if collectible_only:
        cards = _fetch_all_pages(1)
    else:
        cards = []
        for collectible_value in (1, 0):
            cards.extend(_fetch_all_pages(collectible_value))
        # dedupe by id
        by_id: Dict[int, Dict] = {}
        for card in cards:
            card_id = card.get("id")
            if card_id is None:
                continue
            by_id[int(card_id)] = card
        cards = list(by_id.values())

    _write_cache(cache_path, cards, metadata)
    return cards, metadata
