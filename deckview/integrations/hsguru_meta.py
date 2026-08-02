"""
Парсер мета-данных HSGuru для команды /meta.

Источники данных:
  - https://www.hsguru.com/meta?format=N&period=past_3_days&rank=legend
    → архетипы, винрейты, популярность (без кодов колод)
  - https://www.hsguru.com/decks?format=N
    → топ-деки с кодами (основной источник кодов)
  - https://www.hsguru.com/streamer-decks
    → стримерские деки с кодами (дополнительный источник)
  - Google Sheets (ARCHETYPES_SHEET_URL из config)
    → перевод названий EN → RU

Маппинг форматов (HSGuru использует обратный порядок):
  Наш UI format_id=1 → Стандарт → HSGuru format=2
  Наш UI format_id=2 → Вольный  → HSGuru format=1
"""
import html as _html
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from deckview.config import CLASS_EMOJI_ID_MAP, HSGURU_META_URL, HSGURU_URL, MODE_EMOJI_ID_MAP, HS_DATA_API_ENABLED
from framework.http_session import get_http_session
from deckview.integrations.hsguru_fetch import load_archetypes, translate_deck_name

try:
    from deckview.integrations.hs_data_api import (
        build_deck_code_index as build_data_api_deck_code_index,
        get_db_decks as get_data_api_db_decks,
        get_meta_strategies as get_data_api_meta_strategies,
        parse_popularity as parse_data_api_popularity,
    )
except Exception:
    build_data_api_deck_code_index = None
    get_data_api_db_decks = None
    get_data_api_meta_strategies = None
    parse_data_api_popularity = None

try:
    import cloudscraper as _cloudscraper
    _has_cloudscraper = True
except ImportError:
    _has_cloudscraper = False

# Один экземпляр на процесс — иначе каждый create_scraper() жрёт FD и открывает browsers.json.
_cloudscraper_instance = None


def _get_cloudscraper():
    global _cloudscraper_instance
    if _cloudscraper_instance is None and _has_cloudscraper:
        _cloudscraper_instance = _cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    return _cloudscraper_instance

# ─────────────────────────── Константы ───────────────────────────────────────

PERIOD = "past_3_days"
RANK   = "legend"
CACHE_TTL = 1800   # 30 минут

# UI format_id → метка и HSGuru URL-параметр
# (на HSGuru format=2 = Стандарт, format=1 = Вольный)
_FORMAT_LABEL:      dict[int, str] = {1: "Стандарт", 2: "Вольный"}
_HSGURU_FORMAT_MAP: dict[int, int] = {1: 2, 2: 1}   # наш id → HSGuru id

# CSS-класс из HTML мета-страницы → русское название (ключ CLASS_EMOJI_ID_MAP)
_CSS_TO_RU_CLASS: dict[str, str] = {
    "demonhunter": "Охотник на демонов",
    "deathknight": "Рыцарь Смерти",
    "warlock":     "Чернокнижник",
    "shaman":      "Шаман",
    "hunter":      "Охотник",
    "warrior":     "Воин",
    "mage":        "Маг",
    "rogue":       "Разбойник",
    "paladin":     "Паладин",
    "priest":      "Жрец",
    "druid":       "Друид",
}

_CLASS_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("death knight", "Рыцарь Смерти"),
    ("demon hunter", "Охотник на демонов"),
    ("warlock", "Чернокнижник"),
    ("shaman", "Шаман"),
    ("hunter", "Охотник"),
    ("warrior", "Воин"),
    ("mage", "Маг"),
    ("rogue", "Разбойник"),
    ("paladin", "Паладин"),
    ("priest", "Жрец"),
    ("druid", "Друид"),
)
_RU_CLASS_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("рыцарь смерти", "Рыцарь Смерти"),
    ("охотник на демонов", "Охотник на демонов"),
    ("чернокниж", "Чернокнижник"),
    ("шаман", "Шаман"),
    ("охотник", "Охотник"),
    ("воин", "Воин"),
    ("маг", "Маг"),
    ("разбой", "Разбойник"),
    ("паладин", "Паладин"),
    ("жрец", "Жрец"),
    ("друид", "Друид"),
)

# Кэши
_meta_cache:     dict[int, tuple[float, list]] = {}    # format_id → (ts, data)
_streamer_cache: Optional[tuple[float, dict]]  = None  # единый стримерский индекс
_decks_cache:    dict[int, tuple[float, dict]] = {}    # hsguru_format → (ts, index)
_META_DISK_CACHE = Path(os.getenv("HSGURU_META_CACHE_PATH", "cache/hsguru_meta_cache.json"))
_METASTATS_STANDARD_URL = os.getenv(
    "METASTATS_STANDARD_META_URL",
    "https://metastats.net/hearthstone/decksbyrank/legend/",
).strip()
_METASTATS_WILD_URL = os.getenv(
    "METASTATS_WILD_META_URL",
    "https://metastats.net/hearthstone/decksbyrank/wild/legend/",
).strip()
_METASTATS_CLASS_PAGES: tuple[tuple[str, str], ...] = (
    ("DeathKnight", "Рыцарь Смерти"),
    ("DemonHunter", "Охотник на демонов"),
    ("Druid", "Друид"),
    ("Hunter", "Охотник"),
    ("Mage", "Маг"),
    ("Paladin", "Паладин"),
    ("Priest", "Жрец"),
    ("Rogue", "Разбойник"),
    ("Shaman", "Шаман"),
    ("Warlock", "Чернокнижник"),
    ("Warrior", "Воин"),
)
_MATCH_STOP_WORDS: set[str] = {"xl", "hl", "renathal", "reno", "highlander"}
_MATCH_CLASS_WORDS: set[str] = {
    "death",
    "knight",
    "demon",
    "hunter",
    "warlock",
    "shaman",
    "druid",
    "rogue",
    "paladin",
    "mage",
    "priest",
    "warrior",
}

# ────────────────────────── HTTP ──────────────────────────────────────────────

def _fetch_url(url: str) -> str:
    """Загружает URL через cloudscraper (если доступен) или requests."""
    if _has_cloudscraper:
        scraper = _get_cloudscraper()
        resp = scraper.get(url, timeout=30)
    else:
        resp = get_http_session().get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.text


def _meta_disk_cache_path() -> Path:
    path = _META_DISK_CACHE
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


def _short_error(error: Exception) -> str:
    text = re.sub(r"\s+", " ", str(error)).strip()
    http_match = re.search(r"\bHTTP\s+(\d{3})\b", text, re.I)
    if http_match:
        return f"HTTP {http_match.group(1)}"
    client_match = re.search(r"\b(\d{3})\s+Client Error:\s*([^:]+)", text, re.I)
    if client_match:
        return f"HTTP {client_match.group(1)} {client_match.group(2).strip()}"
    return text[:220] or error.__class__.__name__


def _save_meta_disk_cache(format_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    path = _meta_disk_cache_path()
    try:
        data = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded

        saved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data[str(format_id)] = {
            "saved_at": saved_at,
            "rows": rows,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[HSGuru Meta] Не удалось сохранить кэш /meta: {_short_error(e)}")


def _load_meta_disk_cache(format_id: int) -> list[dict]:
    path = _meta_disk_cache_path()
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get(str(format_id)) if isinstance(data, dict) else None
        if not isinstance(entry, dict):
            return []
        rows = entry.get("rows")
        if not isinstance(rows, list) or not rows:
            return []

        saved_at = str(entry.get("saved_at") or "")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cached_row = dict(row)
            cached_row["source_cached"] = True
            cached_row.setdefault("source", "persistent_cache")
            if saved_at:
                cached_row.setdefault("source_fetched_at", saved_at)
            result.append(cached_row)
        return result
    except Exception as e:
        print(f"[HSGuru Meta] Не удалось прочитать кэш /meta: {_short_error(e)}")
        return []


def _clean_metastats_name(title: str) -> str:
    name = re.sub(r"\s+#\d+.*$", "", title or "").strip()
    name = re.sub(r"\s+\(Score:.*?\)\s*$", "", name).strip()
    return re.sub(r"\s+", " ", name).strip()


def _get_meta_from_metastats(format_id: int) -> list[dict]:
    """Emergency fallback from MetaStats when HSGuru/Data API are down."""
    format_id = int(format_id)
    if format_id not in (1, 2):
        return []

    groups: dict[str, dict] = {}
    total_games = 0
    seen_decks: set[str] = set()
    headers = {
        "User-Agent": "Mozilla/5.0 Deckview/1.0 (+https://hs-manacost.ru)",
        "Accept": "text/html,application/xhtml+xml",
    }
    if format_id == 1:
        if not _METASTATS_STANDARD_URL:
            return []
        pages: list[tuple[str, str]] = [(_METASTATS_STANDARD_URL, "")]
        pages.extend(
            (f"https://metastats.net/hearthstone/class/decks/{slug}/", ru_class)
            for slug, ru_class in _METASTATS_CLASS_PAGES
        )
    else:
        if not _METASTATS_WILD_URL:
            return []
        pages = [(_METASTATS_WILD_URL, "")]

    for url, page_ru_class in pages:
        try:
            resp = get_http_session().get(url, headers=headers, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[HSGuru Meta] MetaStats fallback page failed {url}: {_short_error(e)}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for decklist in soup.find_all(class_="decklist"):
            h4 = decklist.find("h4")
            if not h4:
                continue
            title = h4.get_text(" ", strip=True)
            name_en = _clean_metastats_name(title)
            if not name_en:
                continue

            text = decklist.get_text(" ", strip=True)
            games = 0
            games_match = re.search(r"#Games:\s*([\d,]+)", text, re.I)
            if games_match:
                try:
                    games = int(games_match.group(1).replace(",", ""))
                except ValueError:
                    games = 0

            winrate_value = 0.0
            winrate_match = re.search(r"#Win\s*Rate:\s*([\d.]+)\s*%", text, re.I)
            if winrate_match:
                try:
                    winrate_value = float(winrate_match.group(1))
                except ValueError:
                    winrate_value = 0.0

            deck_code = ""
            clip = decklist.find(class_="copytoclipboard")
            if clip and clip.get("data-clipboard-text"):
                code_match = re.search(r"\bAAE[A-Za-z0-9+/]{40,}={0,3}", clip["data-clipboard-text"])
                deck_code = code_match.group(0) if code_match else ""

            dedupe_key = deck_code or f"{title}:{games}"
            if dedupe_key in seen_decks:
                continue
            seen_decks.add(dedupe_key)

            key = name_en.lower()
            row = groups.setdefault(
                key,
                {
                    "name_en": name_en,
                    "games": 0,
                    "weighted_wr": 0.0,
                    "best_code": "",
                    "best_code_games": -1,
                    "ru_class": page_ru_class,
                },
            )
            row["games"] += games
            row["weighted_wr"] += winrate_value * games
            if page_ru_class and not row.get("ru_class"):
                row["ru_class"] = page_ru_class
            if deck_code and games >= row["best_code_games"]:
                row["best_code"] = deck_code
                row["best_code_games"] = games
            total_games += games

    if not groups:
        return []

    try:
        arch_tr = load_archetypes()
    except Exception as e:
        print(f"[HSGuru Meta] Ошибка переводов для MetaStats fallback: {e}")
        arch_tr = {}

    result = []
    for row in sorted(groups.values(), key=lambda item: item["games"], reverse=True):
        games = int(row["games"] or 0)
        if games <= 0:
            continue
        name_en = row["name_en"]
        winrate = row["weighted_wr"] / games if games else 0.0
        ru_class = row.get("ru_class") or _infer_ru_class_any(name_en, translate_deck_name(name_en, arch_tr))
        result.append({
            "name_en": name_en,
            "name_ru": translate_deck_name(name_en, arch_tr) or name_en,
            "winrate": f"{winrate:.1f}%" if winrate else "",
            "game_count": games,
            "popularity": f"{games / total_games * 100:.1f}%" if total_games else "",
            "ru_class": ru_class,
            "emoji_id": CLASS_EMOJI_ID_MAP.get(ru_class, ""),
            "deck_code": row["best_code"],
            "source": "metastats_direct",
        })

    print(f"[HSGuru Meta] source=metastats_direct format_id={format_id} rows={len(result)}")
    return result


# ─────────────────── CSS-класс → класс колоды ────────────────────────────────

def _extract_css_class(td) -> str:
    classes = td.get("class", []) if td else []
    for c in classes:
        if c in _CSS_TO_RU_CLASS:
            return c
    return "unknown"


# ─────────────────── Парсинг /meta?format=N ──────────────────────────────────

def parse_meta_archetypes(html: str) -> list[dict]:
    """
    Парсит HTML страницы /meta и возвращает список архетипов.
    Каждый элемент: {name_en, css_class, winrate, game_count, popularity}
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for row in soup.select("table tbody tr"):
        try:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            link = row.select_one("a.deck-title")
            if not link:
                continue
            name_en = link.get_text(strip=True)
            if not name_en:
                continue

            info_td  = row.select_one("td.decklist-info")
            css_class = _extract_css_class(info_td)

            # Винрейт: HSGuru не пишет % в HTML — добавляем вручную
            winrate = ""
            tag_span = row.select_one("span.tag")
            if tag_span:
                inner = tag_span.get_text(strip=True)
                m = re.search(r"([\d.]+)%?", inner)
                winrate = f"{m.group(1)}%" if m else inner

            # Популярность и кол-во игр: "19.5% (21 539)"
            td2_text  = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            popularity = ""
            game_count = 0
            m_pop   = re.match(r"([\d.]+%)", td2_text)
            if m_pop:
                popularity = m_pop.group(1)
            m_games = re.search(r"\(([\d\s,\xa0]+)\)", td2_text)
            if m_games:
                raw = re.sub(r"[\s,\xa0]", "", m_games.group(1))
                try:
                    game_count = int(raw)
                except ValueError:
                    pass

            results.append({
                "name_en":    name_en,
                "css_class":  css_class,
                "winrate":    winrate,
                "game_count": game_count,
                "popularity": popularity,
            })
        except Exception:
            continue
    return results


# ─────────────────── Индекс кодов из /decks?format=N ─────────────────────────

def _parse_deck_index(html: str) -> dict[str, str]:
    """
    Общий парсер для страниц с кодами колод (/decks и /streamer-decks).
    Поддерживает оба layout: table-based (/streamer-decks) и card-based (/decks).
    Возвращает {english_name_lower: deck_code} (первая = лучшая/свежая).
    """
    soup  = BeautifulSoup(html, "html.parser")
    index: dict[str, str] = {}

    def _add(name_en: str, deck_code: str) -> None:
        key = name_en.strip().lower()
        if key and deck_code and key not in index:
            index[key] = deck_code

    # --- Layout 1: table rows (/streamer-decks) ---
    for row in soup.select("table tbody tr"):
        try:
            clip = row.select_one("[data-clipboard-text]")
            if not clip:
                continue
            deck_code = clip.get("data-clipboard-text", "").strip()
            if not deck_code:
                continue
            deck_link = row.select_one('a[href^="/deck/"]')
            if not deck_link:
                continue
            _add(deck_link.get_text(strip=True), deck_code)
        except Exception:
            continue

    # --- Layout 2: card divs (/decks?format=N) ---
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
            _add(deck_link.get_text(strip=True), deck_code)
        except Exception:
            continue

    return index


def _get_decks_index(hsguru_format: int) -> dict[str, str]:
    """Индекс кодов из /decks?format=N с кэшем 30 мин."""
    now = time.monotonic()
    cached = _decks_cache.get(hsguru_format)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        html  = _fetch_url(f"https://www.hsguru.com/decks?format={hsguru_format}")
        index = _parse_deck_index(html)
        _decks_cache[hsguru_format] = (now, index)
        return index
    except Exception as e:
        print(f"[HSGuru Meta] /decks?format={hsguru_format} ошибка: {e}")
        return {}


def _get_streamer_index() -> dict[str, str]:
    """Индекс кодов из /streamer-decks с кэшем 30 мин."""
    global _streamer_cache
    now = time.monotonic()
    if _streamer_cache and now - _streamer_cache[0] < CACHE_TTL:
        return _streamer_cache[1]
    try:
        html  = _fetch_url(HSGURU_URL)
        index = _parse_deck_index(html)
        _streamer_cache = (now, index)
        return index
    except Exception as e:
        print(f"[HSGuru Meta] /streamer-decks ошибка: {e}")
        return {}


def _build_combined_index(hsguru_format: int) -> dict[str, str]:
    """
    Объединённый индекс из трёх источников:
      1. /decks?format=<hsguru_format>  — топ-деки нужного формата
      2. /decks?<другой формат>          — на случай пересечений архетипов
      3. /streamer-decks                  — стримерские деки (Mixed форматы)
    Приоритет: первый источник (decks нужного формата) имеет приоритет.
    """
    # Сначала собираем из всех источников (приоритет — слева)
    other_format = 1 if hsguru_format == 2 else 2
    primary  = _get_decks_index(hsguru_format)
    secondary = _get_decks_index(other_format)
    streamer  = _get_streamer_index()

    # Мержим: primary побеждает, затем secondary, затем streamer
    combined: dict[str, str] = {}
    for src in (streamer, secondary, primary):   # порядок: сначала низкий приоритет
        for k, v in src.items():
            combined[k] = v
    return combined


# ─────────────────────── Матчинг кодов ───────────────────────────────────────

def _normalize_match_name(value: object) -> str:
    text = str(value or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_match_name(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _candidate_code_names(name_en: str) -> list[str]:
    """Return likely API/DB spellings for HSGuru archetype names."""
    base = _normalize_match_name(name_en)
    variants: list[str] = [base]

    exact_aliases: dict[str, tuple[str, ...]] = {
        "dude paladin": ("silver hand paladin",),
        "token druid": ("aggro druid",),
        "end of turnadin": ("end of turn paladin",),
        "rafaamlock": ("rafaam warlock",),
        "discolock": ("discard warlock",),
        "seedlock": ("seed warlock", "renathal seed warlock"),
        "xl seedlock": ("renathal seed warlock", "seed warlock"),
        "boarlock": ("elwynn boar warlock", "boar warlock"),
        "boar hunter": ("elwynn boar hunter",),
        "hl velarok rogue": ("velarok rogue", "reno velarok rogue"),
        "xl mill druid": ("renathal mill druid", "mill druid"),
        "xl hl shudder shaman": (
            "renathal reno shudderwock shaman",
            "renathal shudderwock shaman",
            "shudderwock shaman",
        ),
        "xl hl tick tock warlock": (
            "renathal reno tick tock warlock",
            "renathal tick tock warlock",
            "tick tock warlock",
        ),
        "xl tick tock warlock": ("renathal tick tock warlock", "tick tock warlock"),
        "xl hl exodia mage": ("renathal reno exodia mage", "renathal exodia mage"),
        "xl exodia mage": ("renathal exodia mage",),
        "xl jtu quest mage": ("renathal jtu quest mage", "renathal quest mage", "jtu quest mage"),
        "xl hl tog druid": ("renathal reno togwaggle druid", "renathal togwaggle druid", "togwaggle druid"),
    }
    variants.extend(exact_aliases.get(base, ()))

    def add_prefix_variants(value: str) -> None:
        if value.startswith("xl hl "):
            rest = value[6:]
            variants.extend((f"renathal reno {rest}", f"renathal {rest}", rest))
        if value.startswith("xl "):
            rest = value[3:]
            variants.extend((f"renathal {rest}", rest))
        if value.startswith("hl "):
            rest = value[3:]
            variants.extend((f"reno {rest}", rest))
        if " highlander " in f" {value} ":
            variants.append(re.sub(r"\bhighlander\b", "reno", value))

    for value in list(variants):
        add_prefix_variants(value)

    replacements: tuple[tuple[str, str], ...] = (
        (r"\bharold\b", "herald"),
        (r"\bdk\b", "death knight"),
        (r"\bdh\b", "demon hunter"),
        (r"\bdisco warlock\b", "discard warlock"),
        (r"\bturnadin\b", "turn paladin"),
        (r"\bshudder\b", "shudderwock"),
        (r"\btog\b", "togwaggle"),
        (r"\bcta\b", "call to arms"),
    )
    for value in list(variants):
        changed = value
        for pattern, replacement in replacements:
            changed = re.sub(pattern, replacement, changed)
        if changed != value:
            variants.append(changed)
            add_prefix_variants(changed)

    for value in list(variants):
        if value.endswith("lock") and " " not in value:
            prefix = value[:-4]
            if prefix:
                variants.append(f"{prefix} warlock")
                if prefix == "disco":
                    variants.append("discard warlock")
                if prefix == "boar":
                    variants.append("elwynn boar warlock")
                if prefix == "seed":
                    variants.append("renathal seed warlock")

    return _ordered_unique(variants)


def _meaningful_match_words(value: str) -> set[str]:
    return {word for word in _normalize_match_name(value).split() if word not in _MATCH_STOP_WORDS}


def _has_distinctive_overlap(left: str, right: str, min_overlap: int = 2) -> bool:
    overlap = _meaningful_match_words(left) & _meaningful_match_words(right)
    return len(overlap) >= min_overlap and bool(overlap - _MATCH_CLASS_WORDS)


def _match_code(arch_name_en: str, index: dict[str, str]) -> Optional[str]:
    """
    Многоуровневый поиск кода колоды:
    1. Точное совпадение
    2. Подстрочное (ключ в имени или имя в ключе), выбираем самый длинный
    3. Пословное пересечение (≥ 2 совпадающих слов)
    """
    candidates = _candidate_code_names(arch_name_en)
    normalized_index: dict[str, str] = {}
    for key, code in index.items():
        normalized_key = _normalize_match_name(key)
        if normalized_key and normalized_key not in normalized_index:
            normalized_index[normalized_key] = code

    # 1. Точное
    for name_lower in candidates:
        if name_lower in index:
            return index[name_lower]
        if name_lower in normalized_index:
            return normalized_index[name_lower]

    # 2. Подстрочное (longest-first)
    best_key, best_len = None, 0
    for name_lower in candidates:
        for key in normalized_index:
            if key in name_lower or name_lower in key:
                if len(key) > best_len:
                    best_key, best_len = key, len(key)
    if best_key:
        return normalized_index[best_key]

    # 3. Пословное пересечение (≥ 2 слова)
    best_key, best_overlap = None, 1
    for name_lower in candidates:
        name_words = _meaningful_match_words(name_lower)
        for key in normalized_index:
            key_words = _meaningful_match_words(key)
            overlap_words = name_words & key_words
            if not overlap_words - _MATCH_CLASS_WORDS:
                continue
            overlap = len(overlap_words)
            if overlap > best_overlap:
                best_overlap, best_key = overlap, key
    if best_key:
        return normalized_index[best_key]

    return None


def _format_name_for_data_api(format_id: int) -> str:
    return "Standard" if int(format_id) == 1 else "Wild"


def _db_match_score(row: dict, candidates: list[str], format_name: str) -> int:
    archetype = _normalize_match_name(row.get("archetype"))
    title = _normalize_match_name(row.get("title"))
    score = 0
    if archetype in candidates:
        score += 120
    elif any(candidate and (candidate in archetype or archetype in candidate) for candidate in candidates):
        score += 80
    elif any(_has_distinctive_overlap(candidate, archetype) for candidate in candidates):
        score += 45

    if title in candidates:
        score += 40
    elif any(candidate and candidate in title for candidate in candidates):
        score += 25

    if str(row.get("format") or "").lower() == format_name.lower():
        score += 20

    source_priority = {
        "hearthstone_decks": 12,
        "vicious_syndicate_radars": 8,
        "metastats_decks": 6,
    }
    score += source_priority.get(str(row.get("source_id") or ""), 0)
    return score


def _match_db_code(arch_name_en: str, format_id: int) -> Optional[str]:
    if not HS_DATA_API_ENABLED or get_data_api_db_decks is None:
        return None

    candidates = _candidate_code_names(arch_name_en)
    format_name = _format_name_for_data_api(format_id)
    seen_queries: set[tuple[str, str]] = set()
    best_code = ""
    best_score = -1

    for query in candidates:
        for fmt in (format_name, ""):
            key = (query, fmt)
            if key in seen_queries:
                continue
            seen_queries.add(key)
            try:
                rows = get_data_api_db_decks(q=query, format_name=fmt, limit=12)
            except Exception as e:
                print(f"[HSGuru Meta] DB deck lookup failed for {query!r}: {_short_error(e)}")
                continue

            for row in rows:
                code = str(row.get("deck_code") or "").strip()
                if not code:
                    continue
                score = _db_match_score(row, candidates, format_name)
                if score > best_score:
                    best_score = score
                    best_code = code

            if best_score >= 120:
                return best_code

    return best_code if best_score >= 45 else None


def _infer_ru_class(name_en: str) -> str:
    lowered = f" {str(name_en or '').lower().replace('-', ' ').replace('ё', 'е')} "
    lowered = re.sub(r"[^0-9a-zа-я]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    if " dh " in lowered:
        return "Охотник на демонов"
    if " dk " in lowered:
        return "Рыцарь Смерти"
    for hint, ru_class in _CLASS_NAME_HINTS:
        if hint in lowered:
            return ru_class
    for hint, ru_class in _RU_CLASS_NAME_HINTS:
        if hint in lowered:
            return ru_class
    return ""


def _infer_ru_class_any(*names: object) -> str:
    for name in names:
        ru_class = _infer_ru_class(str(name or ""))
        if ru_class:
            return ru_class
    return ""


def _normalize_winrate(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"([\d.]+)%?", text)
    if not match:
        return text
    return f"{match.group(1)}%"


def _get_meta_from_data_api(format_id: int) -> list[dict]:
    if (
        not HS_DATA_API_ENABLED
        or get_data_api_meta_strategies is None
        or build_data_api_deck_code_index is None
        or parse_data_api_popularity is None
    ):
        return []

    rows, stats = get_data_api_meta_strategies(format_id)
    if not rows:
        return []

    deck_index = build_data_api_deck_code_index()
    try:
        arch_tr = load_archetypes()
    except Exception as e:
        print(f"[HSGuru Meta] Ошибка переводов: {e}")
        arch_tr = {}

    result = []
    for row in rows:
        name_en = str(row.get("Archetype") or row.get("archetype") or row.get("name") or "").strip()
        if not name_en:
            continue
        name_ru = translate_deck_name(name_en, arch_tr) or name_en
        ru_class = _infer_ru_class_any(name_en, name_ru)
        emoji_id = CLASS_EMOJI_ID_MAP.get(ru_class, "")
        popularity, game_count = parse_data_api_popularity(row.get("Popularity") or row.get("popularity"))
        deck_code = _match_code(name_en, deck_index) or _match_db_code(name_en, format_id)

        result.append({
            "name_en": name_en,
            "name_ru": name_ru,
            "winrate": _normalize_winrate(row.get("Winrate↓") or row.get("Winrate") or row.get("winrate")),
            "game_count": game_count,
            "popularity": popularity,
            "ru_class": ru_class,
            "emoji_id": emoji_id,
            "deck_code": deck_code,
            "source": "hs_data_api",
            "source_id": stats.get("source_id"),
            "source_fetched_at": stats.get("fetched_at"),
        })

    print(
        f"[HSGuru Meta] source=hs_data_api format_id={format_id} "
        f"source_id={stats.get('source_id')} rows={len(result)}"
    )
    return result


# ─────────────────────── Основная функция ────────────────────────────────────

def get_meta(format_id: int) -> list[dict]:
    """
    Возвращает список архетипов для format_id (1=Стандарт, 2=Вольный — с точки зрения UI).
    Результат кэшируется на CACHE_TTL секунд.
    """
    now    = time.monotonic()
    cached = _meta_cache.get(format_id)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    # Маппинг UI → HSGuru URL-параметр
    hsguru_fmt = _HSGURU_FORMAT_MAP.get(format_id, format_id)
    source_errors = []

    try:
        data_api_result = _get_meta_from_data_api(format_id)
        if data_api_result:
            _meta_cache[format_id] = (now, data_api_result)
            _save_meta_disk_cache(format_id, data_api_result)
            return data_api_result
    except Exception as e:
        source_errors.append(f"HS Data API: {_short_error(e)}")
        print(f"[HSGuru Meta] HS Data API fallback to direct HSGuru: {_short_error(e)}")

    try:
        # 1. Парсим мета-страницу (архетипы + статистика)
        meta_url   = f"{HSGURU_META_URL}?format={hsguru_fmt}&period={PERIOD}&rank={RANK}"
        meta_html  = _fetch_url(meta_url)
        archetypes = parse_meta_archetypes(meta_html)

        # 2. Комбинированный индекс кодов
        deck_index = _build_combined_index(hsguru_fmt)

        # 3. Переводы EN→RU
        try:
            arch_tr = load_archetypes()
        except Exception as e:
            print(f"[HSGuru Meta] Ошибка переводов: {e}")
            arch_tr = {}

        # 4. Собираем результат
        result = []
        for arch in archetypes:
            name_en  = arch["name_en"]
            name_ru  = translate_deck_name(name_en, arch_tr) or name_en
            ru_class = _CSS_TO_RU_CLASS.get(arch["css_class"], "")
            emoji_id = CLASS_EMOJI_ID_MAP.get(ru_class, "")
            deck_code = _match_code(name_en, deck_index) or _match_db_code(name_en, format_id)

            result.append({
                "name_en":    name_en,
                "name_ru":    name_ru,
                "winrate":    arch["winrate"],
                "game_count": arch["game_count"],
                "popularity": arch["popularity"],
                "ru_class":   ru_class,
                "emoji_id":   emoji_id,
                "deck_code":  deck_code,
                "source":     "hsguru",
            })

        _meta_cache[format_id] = (now, result)
        _save_meta_disk_cache(format_id, result)
        return result
    except Exception as e:
        source_errors.append(f"HSGuru: {_short_error(e)}")

    try:
        metastats_result = _get_meta_from_metastats(format_id)
        if metastats_result:
            _meta_cache[format_id] = (now, metastats_result)
            _save_meta_disk_cache(format_id, metastats_result)
            return metastats_result
        if int(format_id) == 1:
            source_errors.append("MetaStats: пустой ответ")
    except Exception as e:
        source_errors.append(f"MetaStats: {_short_error(e)}")

    cached_result = _load_meta_disk_cache(format_id)
    if cached_result:
        print(
            f"[HSGuru Meta] source=persistent_cache format_id={format_id} "
            f"rows={len(cached_result)} errors={'; '.join(source_errors)}"
        )
        _meta_cache[format_id] = (now, cached_result)
        return cached_result

    details = "; ".join(source_errors) or "нет доступных источников"
    raise RuntimeError(f"внешние источники меты недоступны, локального кэша пока нет ({details})")


# ─────────────────────── Форматирование сообщения ────────────────────────────

def format_meta_message(meta_list: list[dict], format_id: int) -> str:
    """Строит HTML-сообщение для команды /meta."""
    format_label = _FORMAT_LABEL.get(format_id, "Стандарт")
    mode_emoji_id = MODE_EMOJI_ID_MAP.get(format_label, "")
    mode_icon = (
        f'<tg-emoji emoji-id="{mode_emoji_id}">🎮</tg-emoji>'
        if mode_emoji_id else "🎮"
    )

    lines = [
        f"{mode_icon} <b>Мета Hearthstone — {format_label}</b>",
        "📅 Легенда · последние 3 дня\n",
        "──────────────────────",
    ]

    for i, arch in enumerate(meta_list[:15], 1):
        ru_class = arch.get("ru_class") or _infer_ru_class_any(arch.get("name_en"), arch.get("name_ru"))
        emoji_id = arch.get("emoji_id") or CLASS_EMOJI_ID_MAP.get(ru_class, "")
        cls_icon = (
            f'<tg-emoji emoji-id="{emoji_id}">🛡️</tg-emoji>'
            if emoji_id else "🃏"
        )
        name  = _html.escape(arch["name_ru"])
        wr    = arch["winrate"] or "—"
        games = f'{arch["game_count"]:,}'.replace(",", "\u202f")
        pop   = arch["popularity"] or "—"

        lines.append(f"{i}. {cls_icon} <b>{name}</b>")
        lines.append(f"   📊 {wr} WR · {games} игр · {pop}")

        if arch["deck_code"]:
            lines.append(f"   <code>{arch['deck_code']}</code>")
        else:
            lines.append("   <i>— код не найден</i>")

        lines.append("")

    if any(arch.get("source_cached") for arch in meta_list):
        source_label = "локальный кэш / hs-manacost data api"
    elif any(arch.get("source") == "hs_data_api" for arch in meta_list):
        source_label = "hs-manacost data api / HSGuru"
    elif any(arch.get("source") == "metastats_direct" for arch in meta_list):
        source_label = "metastats.net fallback"
    else:
        source_label = "hsguru.com"

    lines += [
        "──────────────────────",
        f"<i>Обновляется раз в 30 мин · источник: {source_label}</i>",
    ]
    return "\n".join(lines)
