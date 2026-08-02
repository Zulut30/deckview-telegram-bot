"""
Archetype recognition for deck codes using HSGuru as the primary source.

HSGuru's deck-info endpoint is protected by Cloudflare for plain requests, so
the first network path uses cloudscraper. DrissionPage is kept as a browser
fallback for deployments where HSGuru changes its challenge behavior.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from config import (
    HSGURU_ARCHETYPE_API_URL,
    HSGURU_ARCHETYPE_BROWSER_FALLBACK,
    HSGURU_ARCHETYPE_BROWSER_PATH,
    HSGURU_ARCHETYPE_BROWSER_TIMEOUT,
    HSGURU_ARCHETYPE_CACHE_HOURS,
    HSGURU_ARCHETYPE_CACHE_PATH,
    HSGURU_ARCHETYPE_TIMEOUT,
)
from hsguru_fetch import load_archetypes, translate_deck_name

try:
    import cloudscraper

    _HAS_CLOUDSCRAPER = True
except ImportError:
    cloudscraper = None
    _HAS_CLOUDSCRAPER = False


_CACHE_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None
_SCRAPER = None


class HSGuruArchetypeError(RuntimeError):
    pass


def _cache_path() -> Path:
    path = Path(HSGURU_ARCHETYPE_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cache() -> Dict[str, Any]:
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is not None:
            return _CACHE
        path = _cache_path()
        if not path.exists():
            _CACHE = {}
            return _CACHE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _CACHE = data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[HSGuru Archetype] Не удалось прочитать кэш {path}: {e}")
            _CACHE = {}
        return _CACHE


def _save_cache() -> None:
    path = _cache_path()
    with _CACHE_LOCK:
        data = _CACHE or {}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _get_cached(deck_code: str) -> Optional[Dict[str, Any]]:
    if HSGURU_ARCHETYPE_CACHE_HOURS <= 0:
        return None
    item = _load_cache().get(deck_code)
    if not isinstance(item, dict):
        return None
    cached_at = float(item.get("cached_at") or 0)
    max_age = HSGURU_ARCHETYPE_CACHE_HOURS * 3600
    if time.time() - cached_at > max_age:
        return None
    result = dict(item.get("result") or {})
    if result:
        raw = str(result.get("archetype_raw") or result.get("deck_name_raw") or "").strip()
        if raw:
            translated, translation = _translate_archetype(raw)
            result["archetype"] = translated or raw
            result["translation"] = translation
        result["cached"] = True
        result["source"] = "cache"
        return result
    return None


def get_cached_archetype(deck_code: str) -> Optional[Dict[str, Any]]:
    """Return cached recognition only; never perform network or browser I/O."""
    return _get_cached(str(deck_code or "").strip())


def _put_cached(deck_code: str, result: Dict[str, Any]) -> None:
    if HSGURU_ARCHETYPE_CACHE_HOURS <= 0 or not result.get("success"):
        return
    stored = dict(result)
    stored["cached"] = False
    _load_cache()[deck_code] = {
        "cached_at": time.time(),
        "result": stored,
    }
    try:
        _save_cache()
    except Exception as e:
        print(f"[HSGuru Archetype] Не удалось сохранить кэш: {e}")


def _deck_info_url(deck_code: str) -> str:
    return f"{HSGURU_ARCHETYPE_API_URL}/{quote(deck_code, safe='')}"


def _get_cloudscraper():
    global _SCRAPER
    if _SCRAPER is None and _HAS_CLOUDSCRAPER:
        _SCRAPER = cloudscraper.create_scraper()
    return _SCRAPER


def _fetch_with_cloudscraper(
    deck_code: str,
    network_timeout: float | None = None,
) -> Dict[str, Any]:
    url = _deck_info_url(deck_code)
    scraper = _get_cloudscraper()
    session = scraper or requests
    response = session.get(
        url,
        timeout=(
            HSGURU_ARCHETYPE_TIMEOUT
            if network_timeout is None
            else max(0.1, float(network_timeout))
        ),
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/125.0 Safari/537.36"
            ),
        },
    )
    if response.status_code >= 400:
        snippet = response.text[:160].replace("\n", " ")
        raise HSGuruArchetypeError(f"HSGuru HTTP {response.status_code}: {snippet}")
    try:
        data = response.json()
    except ValueError as e:
        snippet = response.text[:160].replace("\n", " ")
        raise HSGuruArchetypeError(f"HSGuru returned non-JSON response: {snippet}") from e
    if not isinstance(data, dict):
        raise HSGuruArchetypeError("HSGuru returned unexpected JSON shape")
    return data


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    value = (text or "").strip()
    if not value:
        raise HSGuruArchetypeError("DrissionPage returned empty page")
    try:
        data = json.loads(value)
    except ValueError as e:
        snippet = value[:160].replace("\n", " ")
        raise HSGuruArchetypeError(f"DrissionPage returned non-JSON response: {snippet}") from e
    if not isinstance(data, dict):
        raise HSGuruArchetypeError("DrissionPage returned unexpected JSON shape")
    return data


def _fetch_with_drissionpage(deck_code: str) -> Dict[str, Any]:
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError as e:
        raise HSGuruArchetypeError(f"DrissionPage is not installed: {e}") from e

    url = _deck_info_url(deck_code)
    options = ChromiumOptions()
    if HSGURU_ARCHETYPE_BROWSER_PATH and os.path.exists(HSGURU_ARCHETYPE_BROWSER_PATH):
        options.set_browser_path(HSGURU_ARCHETYPE_BROWSER_PATH)
    options.headless(True)
    options.no_imgs()
    for arg in (
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1280,720",
    ):
        options.set_argument(arg)

    page = ChromiumPage(addr_or_opts=options)
    try:
        page.get(url, timeout=HSGURU_ARCHETYPE_BROWSER_TIMEOUT)
        deadline = time.time() + HSGURU_ARCHETYPE_BROWSER_TIMEOUT
        last_text = ""
        while time.time() < deadline:
            body = page.ele("tag:body")
            last_text = body.text if body else ""
            if last_text.strip().startswith("{"):
                return _extract_json_from_text(last_text)
            time.sleep(1)
        return _extract_json_from_text(last_text)
    finally:
        page.quit()


def _first_str(data: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _translate_archetype(name: str) -> tuple[str, str]:
    if not name:
        return "", "none"
    translations = load_archetypes()
    key = name.lower().strip()
    if key in translations:
        return translations[key], "exact"
    translated = translate_deck_name(name, translations)
    if translated != name:
        return translated, "partial"
    return name, "none"


def _normalize_result(deck_code: str, data: Dict[str, Any], source: str) -> Dict[str, Any]:
    raw_archetype = _first_str(data, "archetype", "archetypeName", "archetype_name")
    raw_name = _first_str(data, "name", "deckName", "deck_name")
    raw = raw_archetype or raw_name
    translated, translation = _translate_archetype(raw)

    return {
        "success": bool(raw),
        "source": source,
        "cached": False,
        "deck_code": deck_code,
        "archetype": translated or raw,
        "archetype_raw": raw_archetype or raw,
        "deck_name_raw": raw_name or raw,
        "class": _first_str(data, "class", "playerClass", "player_class"),
        "format": _first_str(data, "format", "gameMode", "mode"),
        "translation": translation,
        "error": None if raw else "HSGuru did not return archetype",
    }


def recognize_archetype(
    deck_code: str,
    *,
    use_cache: bool = True,
    network_timeout: float | None = None,
) -> Dict[str, Any]:
    """Return a public-safe archetype recognition result for a Hearthstone deck code."""
    code = str(deck_code or "").strip()
    if not code:
        return {
            "success": False,
            "source": "hsguru",
            "cached": False,
            "deck_code": "",
            "archetype": "",
            "archetype_raw": "",
            "deck_name_raw": "",
            "class": "",
            "format": "",
            "translation": "none",
            "error": "deck_code required",
        }

    if use_cache:
        cached = _get_cached(code)
        if cached:
            return cached

    errors: list[str] = []
    for source, fetcher in (("hsguru_cloudscraper", _fetch_with_cloudscraper),):
        try:
            result = _normalize_result(
                code,
                fetcher(code, network_timeout=network_timeout),
                source,
            )
            if result.get("success"):
                _put_cached(code, result)
            return result
        except Exception as e:
            errors.append(f"{source}: {e}")

    if HSGURU_ARCHETYPE_BROWSER_FALLBACK:
        try:
            result = _normalize_result(code, _fetch_with_drissionpage(code), "hsguru_drissionpage")
            if result.get("success"):
                _put_cached(code, result)
            return result
        except Exception as e:
            errors.append(f"hsguru_drissionpage: {e}")

    return {
        "success": False,
        "source": "hsguru",
        "cached": False,
        "deck_code": code,
        "archetype": "",
        "archetype_raw": "",
        "deck_name_raw": "",
        "class": "",
        "format": "",
        "translation": "none",
        "error": " | ".join(errors)[:500] or "HSGuru archetype lookup failed",
    }
