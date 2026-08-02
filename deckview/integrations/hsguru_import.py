"""
Массовый импорт колод с HSGuru в локальную БД.
Парсит страницы /decks?format=N и /meta?format=N,
декодирует коды колод, считает пыль, сохраняет в generated_decks + deck_cards.
Также собирает статистику архетипов (винрейт, игры) в archetype_stats.
"""
import base64
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from db.cards_db import get_card_cost
from deckview.integrations.hsguru_fetch import load_archetypes, translate_deck_name
from deckview.integrations.hsguru_meta import _CSS_TO_RU_CLASS, _fetch_url, parse_meta_archetypes
from deckview.repositories.web import (
    add_generated_with_cards,
    deck_code_exists,
    init_db,
    upsert_archetype_stats,
)

# HSGuru format → internal mode name
_FORMAT_TO_MODE = {2: "Стандарт", 1: "Вольный"}


# ─────────────────── Varint / Deckstring decoder ────────────────────────────

def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Read a varint from bytes starting at offset. Returns (value, new_offset)."""
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, offset
        shift += 7
    raise ValueError("Truncated varint")


def decode_deckstring(deck_code: str) -> tuple[list[tuple[int, int]], int] | None:
    """
    Decodes a Hearthstone deck code (base64 + varint) into list of (dbf_id, quantity)
    pairs and format int. Returns None on decode error.
    """
    try:
        raw = base64.b64decode(deck_code)
    except Exception:
        return None

    try:
        off = 0
        # reserved byte (always 0)
        _reserved, off = _read_varint(raw, off)
        # version (always 1)
        _version, off = _read_varint(raw, off)
        # format: 2=standard, 1=wild
        fmt, off = _read_varint(raw, off)

        # heroes
        hero_count, off = _read_varint(raw, off)
        for _ in range(hero_count):
            _hero_dbf, off = _read_varint(raw, off)

        cards: list[tuple[int, int]] = []

        # single-copy cards
        single_count, off = _read_varint(raw, off)
        for _ in range(single_count):
            dbf_id, off = _read_varint(raw, off)
            cards.append((dbf_id, 1))

        # double-copy cards
        double_count, off = _read_varint(raw, off)
        for _ in range(double_count):
            dbf_id, off = _read_varint(raw, off)
            cards.append((dbf_id, 2))

        # n-copy cards (optional, remaining bytes)
        if off < len(raw):
            n_count, off = _read_varint(raw, off)
            for _ in range(n_count):
                dbf_id, off = _read_varint(raw, off)
                qty, off = _read_varint(raw, off)
                cards.append((dbf_id, qty))

        return cards, fmt
    except Exception:
        return None


# ─────────────────── HTML parser for /decks pages ───────────────────────────

def parse_hsguru_decks_page(html: str) -> list[dict]:
    """
    Parses a single page of HSGuru /decks results.
    Returns list of dicts: {deck_code, name_en, hero_class_css, winrate, games}
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for card in soup.select("div.card"):
        try:
            clip = card.select_one("[data-clipboard-text]")
            if not clip:
                continue
            deck_code = clip.get("data-clipboard-text", "").strip()
            if not deck_code:
                continue

            deck_link = card.select_one('a[href^="/deck/"]')
            if not deck_link:
                continue
            name_en = deck_link.get_text(strip=True)
            if not name_en:
                continue

            # Hero class from .decklist-info CSS class
            hero_class_css = "unknown"
            info_el = card.select_one(".decklist-info")
            if info_el:
                for cls in info_el.get("class", []):
                    if cls in _CSS_TO_RU_CLASS:
                        hero_class_css = cls
                        break

            # Winrate from span.tag
            winrate = 0.0
            tag_span = card.select_one("span.tag")
            if tag_span:
                m = re.search(r"([\d.]+)", tag_span.get_text(strip=True))
                if m:
                    try:
                        winrate = float(m.group(1))
                    except ValueError:
                        pass

            # Games count
            games = 0
            for col in card.select("div.column.tag"):
                text = col.get_text(strip=True)
                m_games = re.search(r"Games:\s*([\d,\s]+)", text, re.IGNORECASE)
                if m_games:
                    raw = re.sub(r"[\s,]", "", m_games.group(1))
                    try:
                        games = int(raw)
                    except ValueError:
                        pass

            results.append({
                "deck_code": deck_code,
                "name_en": name_en,
                "hero_class_css": hero_class_css,
                "winrate": winrate,
                "games": games,
            })
        except Exception:
            continue

    return results


# ─────────────────── Multi-page fetcher ─────────────────────────────────────

def fetch_hsguru_decks(
    hsguru_format: int = 2,
    min_games: int = 50,
    period: str = "past_day",
    rank: str = "all",
    max_decks: int = 1000,
) -> list[dict]:
    """
    Fetches multiple pages from HSGuru /decks.
    Deduplicates by deck_code (keeps first occurrence = highest winrate).
    """
    seen_codes: set[str] = set()
    all_decks: list[dict] = []
    page = 1

    while len(all_decks) < max_decks and page <= 100:
        url = (
            f"https://www.hsguru.com/decks?format={hsguru_format}"
            f"&min_games={min_games}&period={period}&rank={rank}&page={page}"
        )
        try:
            html = _fetch_url(url)
        except Exception as e:
            print(f"[HSGuru Import] Ошибка загрузки стр. {page}: {e}")
            break

        page_decks = parse_hsguru_decks_page(html)
        if not page_decks:
            break

        for d in page_decks:
            if d["deck_code"] not in seen_codes:
                seen_codes.add(d["deck_code"])
                all_decks.append(d)

        if page % 5 == 0:
            print(f"[HSGuru Import] Стр. {page}: загружено {len(all_decks)} деков...")

        page += 1
        time.sleep(1)

    return all_decks[:max_decks]


# ─────────────────── Import decks into DB ───────────────────────────────────

def import_decks_to_db(decks: list[dict]) -> dict:
    """
    For each deck: decode, calculate dust, translate name, insert into DB.
    Returns: {"imported": N, "skipped": N, "errors": N}
    """
    init_db()

    try:
        arch_tr = load_archetypes()
    except Exception:
        arch_tr = {}

    imported = 0
    skipped = 0
    errors = 0

    for deck in decks:
        try:
            deck_code = deck["deck_code"]

            # Check for duplicates
            if deck_code_exists(deck_code):
                skipped += 1
                continue

            # Decode deckstring
            result = decode_deckstring(deck_code)
            if result is None:
                errors += 1
                continue

            cards, fmt = result
            dbf_ids = [dbf_id for dbf_id, _qty in cards]

            # Calculate dust cost
            dust = 0
            for dbf_id, qty in cards:
                card_cost = get_card_cost(dbf_id)
                if card_cost is not None:
                    dust += card_cost * qty

            # Translate name
            name_en = deck["name_en"]
            name_ru = translate_deck_name(name_en, arch_tr) or name_en

            # Map hero class
            hero_class_css = deck.get("hero_class_css", "unknown")
            ru_class = _CSS_TO_RU_CLASS.get(hero_class_css)

            # Map format
            mode = _FORMAT_TO_MODE.get(fmt, "Стандарт")

            # Generate filename
            code_hash = hashlib.md5(deck_code.encode()).hexdigest()[:16]
            filename = f"hsguru:{code_hash}"

            add_generated_with_cards(
                deck_code=deck_code,
                deck_name=name_ru,
                cost=dust,
                filename=filename,
                dbf_ids=dbf_ids,
                source="hsguru_import",
                deck_class=ru_class,
                deck_mode=mode,
            )
            imported += 1

        except Exception as e:
            print(f"[HSGuru Import] Ошибка импорта колоды {deck.get('name_en', '?')}: {e}")
            errors += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ─────────────────── Archetype stats ────────────────────────────────────────

def fetch_and_save_archetype_stats(hsguru_format: int = 2) -> int:
    """
    Fetches /meta page using parse_meta_archetypes and upserts into archetype_stats.
    Returns count of archetypes saved.
    """
    init_db()

    url = f"https://www.hsguru.com/meta?format={hsguru_format}"
    html = _fetch_url(url)
    archetypes = parse_meta_archetypes(html)

    if not archetypes:
        return 0

    try:
        arch_tr = load_archetypes()
    except Exception:
        arch_tr = {}

    mode = _FORMAT_TO_MODE.get(hsguru_format, "Стандарт")
    snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    stats = []
    for arch in archetypes:
        name_en = arch["name_en"]
        name_ru = translate_deck_name(name_en, arch_tr) or name_en
        hero_class = _CSS_TO_RU_CLASS.get(arch.get("css_class", ""))

        stats.append({
            "name_en": name_en,
            "name_ru": name_ru,
            "hero_class": hero_class,
            "format": mode,
            "winrate": float(arch["winrate"].replace("%", "")) if arch.get("winrate") else None,
            "game_count": arch.get("game_count", 0),
            "popularity": arch.get("popularity", ""),
            "snapshot_date": snapshot_date,
        })

    return upsert_archetype_stats(stats)


# ─────────────────── Main entry point ───────────────────────────────────────

def run_daily_import(max_decks: int = 500) -> dict:
    """
    Main entry point called by scheduler.
    Imports decks for BOTH Standard and Wild, also fetches archetype stats.
    """
    init_db()
    summary = {
        "standard_decks": {"imported": 0, "skipped": 0, "errors": 0},
        "wild_decks": {"imported": 0, "skipped": 0, "errors": 0},
        "standard_archetypes": 0,
        "wild_archetypes": 0,
    }

    # Standard (format=2)
    print("[HSGuru Import] Загрузка колод Стандарт...")
    try:
        std_decks = fetch_hsguru_decks(hsguru_format=2, max_decks=max_decks)
        print(f"[HSGuru Import] Стандарт: найдено {len(std_decks)} колод")
        summary["standard_decks"] = import_decks_to_db(std_decks)
        print(f"[HSGuru Import] Стандарт: {summary['standard_decks']}")
    except Exception as e:
        print(f"[HSGuru Import] Ошибка импорта Стандарт: {e}")

    # Wild (format=1)
    print("[HSGuru Import] Загрузка колод Вольный...")
    try:
        wild_decks = fetch_hsguru_decks(hsguru_format=1, max_decks=max_decks)
        print(f"[HSGuru Import] Вольный: найдено {len(wild_decks)} колод")
        summary["wild_decks"] = import_decks_to_db(wild_decks)
        print(f"[HSGuru Import] Вольный: {summary['wild_decks']}")
    except Exception as e:
        print(f"[HSGuru Import] Ошибка импорта Вольный: {e}")

    # Archetype stats
    print("[HSGuru Import] Загрузка статистики архетипов...")
    try:
        summary["standard_archetypes"] = fetch_and_save_archetype_stats(hsguru_format=2)
        print(f"[HSGuru Import] Архетипы Стандарт: {summary['standard_archetypes']}")
    except Exception as e:
        print(f"[HSGuru Import] Ошибка архетипов Стандарт: {e}")

    try:
        summary["wild_archetypes"] = fetch_and_save_archetype_stats(hsguru_format=1)
        print(f"[HSGuru Import] Архетипы Вольный: {summary['wild_archetypes']}")
    except Exception as e:
        print(f"[HSGuru Import] Ошибка архетипов Вольный: {e}")

    return summary
