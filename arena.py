"""
Арена Hearthstone — винрейты классов.

Основной источник: hsreplay.net
  GET https://hsreplay.net/api/v1/arena/classes_stats/
  Ответ: { "data": [ { "deck_class": 4, "win_rate": 53.1, "num_drafts": 2277,
                        "pick_rate": 75.41, "pct_7_plus": 12.52 }, ... ] }

  deck_class — Hearthstone CardClass enum:
    1=DeathKnight, 2=Druid, 3=Hunter, 4=Mage, 5=Paladin,
    6=Priest, 7=Rogue, 8=Shaman, 9=Warlock, 10=Warrior, 14=DemonHunter

Fallback: zerotoheroes.com / firestoneapp.com
  GET https://static.zerotoheroes.com/api/arena/stats/classes/arena/{period}/overview.gz.json
"""

import html
import time

from framework.http_session import get_http_session

try:
    import cloudscraper
    _HAS_CLOUDSCRAPER = True
except ImportError:
    _HAS_CLOUDSCRAPER = False

_cloudscraper_instance = None


def _get_cloudscraper():
    global _cloudscraper_instance
    if _cloudscraper_instance is None and _HAS_CLOUDSCRAPER:
        _cloudscraper_instance = cloudscraper.create_scraper()
    return _cloudscraper_instance

from config import (
    CLASS_EMOJI_ID_MAP,
    MANACOST_PUBLIC_API_BASE_URL,
    MANACOST_PUBLIC_API_KEY,
    MANACOST_PUBLIC_API_TIMEOUT,
)

try:
    from hs_data_api import fetch_dataset as fetch_data_api_dataset
except Exception:
    fetch_data_api_dataset = None

# ─── Константы ────────────────────────────────────────────────────────────────

CACHE_TTL = 3600  # 1 час

# HSReplay — публичный endpoint (не требует авторизации)
_HSREPLAY_API_URL = "https://hsreplay.net/api/v1/arena/classes_stats/"
_HS_DATA_API_ARENA_SOURCE_ID = "hsreplay_arena"
_MANACOST_ARENA_CLASSES_PATH = "/api/v1/arena/statistics/classes"

# Zerotoheroes (fallback)
_ZTH_API_URL = (
    "https://static.zerotoheroes.com/api/arena/stats/classes/arena/{period}/overview.gz.json"
)

PERIOD_LABEL: dict[str, str] = {
    "last-patch":   "Патч",
    "past-7":       "7 дней",
    "past-20":      "20 дней",
    "hsreplay":     "Актуально",
}

# Hearthstone CardClass enum → русское название
_CLASS_ID_TO_RU: dict[int, str] = {
    1:  "Рыцарь Смерти",
    2:  "Друид",
    3:  "Охотник",
    4:  "Маг",
    5:  "Паладин",
    6:  "Жрец",
    7:  "Разбойник",
    8:  "Шаман",
    9:  "Чернокнижник",
    10: "Воин",
    14: "Охотник на демонов",
}

# Для fallback (zerotoheroes возвращает строку)
_EN_TO_RU: dict[str, str] = {
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

_RU_TO_CODE: dict[str, str] = {
    "Рыцарь Смерти": "DK",
    "Охотник на демонов": "DH",
    "Друид": "Dr",
    "Охотник": "Hu",
    "Маг": "Mg",
    "Паладин": "Pa",
    "Жрец": "Pr",
    "Разбойник": "Ro",
    "Шаман": "Sh",
    "Чернокнижник": "Wl",
    "Воин": "Wr",
}

_CODE_LEGEND: tuple[str, ...] = (
    "DK Рыцарь Смерти",
    "DH Охотник на демонов",
    "Dr Друид",
    "Hu Охотник",
    "Mg Маг",
    "Pa Паладин",
    "Pr Жрец",
    "Ro Разбойник",
    "Sh Шаман",
    "Wl Чернокнижник",
    "Wr Воин",
)

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

# ─── Кэш ──────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}


# ─── Загрузка данных ──────────────────────────────────────────────────────────

def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _seven_plus_percent(value, win_rate: float) -> float | None:
    """Normalize a real seven-win rate without turning missing data into zero."""
    if value is None or str(value).strip() == "":
        return None
    percent = _safe_float(value)
    if percent > 50 and percent > win_rate:
        percent /= 100
    return percent


def _class_to_ru(value) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return _CLASS_ID_TO_RU.get(value, "")
    text = str(value).strip()
    if not text:
        return ""
    key = text.lower().replace(" ", "").replace("_", "").replace("-", "")
    return _EN_TO_RU.get(key, text)


def _normalize_class_stats(rows: list[dict], *, source: str) -> list[dict]:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cls_ru = _class_to_ru(row.get("deck_class") if "deck_class" in row and source == "hsreplay" else row.get("class"))
        if not cls_ru and "deck_class" in row:
            cls_ru = _class_to_ru(row.get("deck_class"))
        if not cls_ru:
            continue
        wr = _safe_float(row.get("win_rate"))
        drafts = _safe_int(row.get("num_drafts"))
        if drafts == 0:
            continue
        normalized.append({
            "playerClass": cls_ru,
            "totalGames": drafts,
            "_win_rate": wr,
            "_source": source,
            "_pct_7plus": _safe_float(row.get("pct_7_plus")),
        })
    return normalized


def _normalize_matchups(rows: list[dict], *, source: str) -> list[dict]:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if source == "hsreplay":
            class_a = _class_to_ru(row.get("deck_class"))
            class_b = _class_to_ru(row.get("secondary_deck_class"))
        else:
            class_a = _class_to_ru(row.get("class_a"))
            class_b = _class_to_ru(row.get("class_b"))
        if not class_a or not class_b:
            continue
        normalized.append({
            "class_a": class_a,
            "class_b": class_b,
            "win_rate": _safe_float(row.get("win_rate")),
        })
    return normalized


def _fetch_manacost_public_api() -> dict:
    """Load Arena class statistics from the official Manacost Public API."""
    if not MANACOST_PUBLIC_API_KEY:
        raise RuntimeError("MANACOST_PUBLIC_API_KEY не задан")

    response = get_http_session().get(
        f"{MANACOST_PUBLIC_API_BASE_URL}{_MANACOST_ARENA_CLASSES_PATH}",
        params={"source": "hsreplay"},
        headers={
            "Accept": "application/json",
            "X-API-Key": MANACOST_PUBLIC_API_KEY,
        },
        timeout=MANACOST_PUBLIC_API_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    rows = payload.get("data") if isinstance(payload, dict) else None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manacost Public API: empty arena class stats")
    if not isinstance(meta, dict) or meta.get("entity") != "classes":
        raise ValueError("Manacost Public API: unexpected arena payload")

    stats = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        cls_ru = _class_to_ru(row.get("classId") or row.get("name"))
        games = _safe_int(metrics.get("games"))
        if not cls_ru or games <= 0:
            continue
        win_rate = _safe_float(metrics.get("winratePercent"))
        stats.append({
            "playerClass": cls_ru,
            "totalGames": games,
            "_win_rate": win_rate,
            "_source": "manacost_public_api",
            "_pct_7plus": _seven_plus_percent(
                metrics.get("sevenPlusWinsPercent"),
                win_rate,
            ),
        })

    if not stats:
        raise ValueError("Manacost Public API: no usable arena class stats")

    seven_plus_source = None
    if any(item.get("_pct_7plus") is None for item in stats):
        try:
            direct_hsreplay = _fetch_hsreplay()
            seven_plus_by_class = {
                str(item.get("playerClass") or ""): item.get("_pct_7plus")
                for item in direct_hsreplay.get("stats") or []
                if isinstance(item.get("_pct_7plus"), (int, float))
            }
            for item in stats:
                if item.get("_pct_7plus") is None:
                    item["_pct_7plus"] = seven_plus_by_class.get(item["playerClass"])
            if any(isinstance(item.get("_pct_7plus"), (int, float)) for item in stats):
                seven_plus_source = "hsreplay"
        except Exception as exc:
            print(f"[Arena] Не удалось дополнить статистику 7+ из HSReplay: {exc}")

    return {
        "stats": stats,
        "matchups": [],
        "_period": "hsreplay",
        "_source": "manacost_public_api",
        "_fetched_at": meta.get("updatedAt"),
        "_data_status": meta.get("dataStatus"),
        "_dataset_version": meta.get("datasetVersion"),
        "_seven_plus_source": seven_plus_source,
    }


def _fetch_data_api_arena() -> dict:
    """Loads HSReplay arena classes and dual-class matrix from hs-data-api."""
    if fetch_data_api_dataset is None:
        raise RuntimeError("HS Data API client is unavailable")

    dataset = fetch_data_api_dataset(_HS_DATA_API_ARENA_SOURCE_ID)
    structured = (dataset.get("data") or {}).get("structured") if isinstance(dataset, dict) else None
    if not isinstance(structured, dict) or structured.get("type") != "arena_class_matrix":
        raise ValueError("HS Data API: unexpected arena payload")

    stats = _normalize_class_stats(structured.get("classes") or [], source="hs_data_api")
    matchups = _normalize_matchups(structured.get("matchups") or [], source="hs_data_api")
    if not stats:
        raise ValueError("HS Data API: empty arena class stats")

    return {
        "stats": stats,
        "matchups": matchups,
        "_period": "hsreplay",
        "_source": "hs_data_api",
        "_fetched_at": dataset.get("fetched_at"),
    }

def _fetch_hsreplay() -> dict:
    """Загружает данные арены с hsreplay.net через cloudscraper (обход Cloudflare)."""
    if not _HAS_CLOUDSCRAPER:
        raise ImportError("cloudscraper не установлен")

    scraper = _get_cloudscraper()
    resp = scraper.get(
        _HSREPLAY_API_URL,
        headers={
            "Accept": "application/json",
            "Referer": "https://hsreplay.net/arena/",
        },
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()

    rows = raw.get("data", [])
    if not rows:
        raise ValueError("HSReplay: пустой ответ (нет данных в data[])")

    normalized = _normalize_class_stats(rows, source="hsreplay")
    matchups = _normalize_matchups(raw.get("dual_class_data") or [], source="hsreplay")

    return {
        "stats": normalized,
        "matchups": matchups,
        "_period": "hsreplay",
        "_source": "hsreplay",
    }


def _fetch_zth(period: str) -> dict:
    """Загружает данные арены с zerotoheroes.com (fallback)."""
    url = _ZTH_API_URL.format(period=period)
    resp = get_http_session().get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0",
            "Accept-Encoding": "gzip",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data["_period"] = period
    data["_source"] = "zerotoheroes"
    return data


def get_arena_stats(period: str = "last-patch") -> dict:
    """
    Возвращает данные по классам (с кэшем 1 час).

    Пытается загрузить с hsreplay.net; при ошибке — zerotoheroes.com.
    При любой ошибке загрузки возвращает устаревший кэш, если он есть.
    """
    cache_key = "hsreplay"  # HSReplay всегда даёт 14 дней; period игнорируется как ключ кэша
    now = time.monotonic()

    # Проверяем кэш
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    # Основной источник: официальный Manacost Public API.
    try:
        data = _fetch_manacost_public_api()
        _cache[cache_key] = (now, data)
        print("[Arena] Данные загружены с arena.hs-manacost.ru Public API")
        return data
    except Exception as e:
        print(f"[Arena] Manacost Public API недоступен: {e}")

    # Резервный API-кэш с HSReplay
    try:
        data = _fetch_data_api_arena()
        _cache[cache_key] = (now, data)
        print("[Arena] Данные загружены с api.hs-manacost.ru/hsreplay_arena")
        return data
    except Exception as e:
        print(f"[Arena] HS Data API недоступен: {e}")

    # Пробуем HSReplay напрямую
    try:
        data = _fetch_hsreplay()
        _cache[cache_key] = (now, data)
        print("[Arena] Данные загружены с hsreplay.net")
        return data
    except Exception as e:
        print(f"[Arena] hsreplay.net недоступен: {e}")

    # Fallback: zerotoheroes
    try:
        data = _fetch_zth(period)
        _cache[cache_key] = (now, data)
        print("[Arena] Данные загружены с zerotoheroes.com (fallback)")
        return data
    except Exception as e2:
        print(f"[Arena] zerotoheroes.com недоступен: {e2}")

    # Возвращаем устаревший кэш
    if cached:
        print("[Arena] Используется устаревший кэш")
        return cached[1]

    raise RuntimeError("Не удалось загрузить данные арены ни из одного источника")


def get_arena_matrix() -> dict:
    """Returns HSReplay dual-class arena winrate matrix data."""
    cache_key = "hsreplay_matrix"
    now = time.monotonic()

    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    stats_cached = _cache.get("hsreplay")
    if stats_cached and now - stats_cached[0] < CACHE_TTL and stats_cached[1].get("matchups"):
        _cache[cache_key] = stats_cached
        return stats_cached[1]

    last_error = None
    for loader, label in ((_fetch_data_api_arena, "api.hs-manacost.ru/hsreplay_arena"), (_fetch_hsreplay, "hsreplay.net")):
        try:
            data = loader()
            if not data.get("matchups"):
                raise ValueError("нет dual-class matchups")
            _cache["hsreplay"] = (now, data)
            _cache[cache_key] = (now, data)
            print(f"[Arena] Матрица загружена с {label}")
            return data
        except Exception as e:
            last_error = e
            print(f"[Arena] Матрица недоступна через {label}: {e}")

    if cached:
        print("[Arena] Используется устаревший кэш матрицы")
        return cached[1]

    raise RuntimeError(f"Не удалось загрузить матрицу классов: {last_error}")


# ─── Форматирование ────────────────────────────────────────────────────────────

def _bar(wr: float, width: int = 9) -> str:
    """Мини-прогресс-бар: диапазон 40–70%."""
    filled = round((wr - 40.0) / 30.0 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "▒" * (width - filled)


def format_arena_message(data: dict, period: str) -> str:
    """Строит HTML-сообщение для команды /arena."""
    stats_raw = data.get("stats", [])
    source    = data.get("_source", "zerotoheroes")

    def get_wr(s: dict) -> float:
        if "_win_rate" in s:
            return float(s["_win_rate"])
        games = s.get("totalGames", 0)
        wins  = s.get("totalsWins", 0)
        return (wins / games * 100) if games > 0 else 0.0

    stats = sorted(stats_raw, key=get_wr, reverse=True)

    total_count = sum(s.get("totalGames", 0) for s in stats)
    total_str   = f"{total_count:,}".replace(",", "\u202f")

    used_period  = data.get("_period", period)
    period_label = PERIOD_LABEL.get(used_period, used_period)

    if source == "manacost_public_api":
        source_label = (
            "arena.hs-manacost.ru + hsreplay.net"
            if data.get("_seven_plus_source") == "hsreplay"
            else "arena.hs-manacost.ru"
        )
        count_label = "ранов"
    elif source in ("hsreplay", "hs_data_api"):
        source_label = "hsreplay.net"
        count_label  = "ранов"
    else:
        source_label = "firestoneapp.com"
        count_label  = "партий"

    arena_icon = '<tg-emoji emoji-id="5327887994177225636">🏟️</tg-emoji>'
    lines = [
        f"{arena_icon} <b>Арена — Винрейт классов</b>",
        f"📅 {period_label} · {total_str} {count_label}\n",
        "──────────────────────",
    ]

    for i, s in enumerate(stats, 1):
        cls_raw = s.get("playerClass", "")
        # HSReplay уже возвращает русское название; zerotoheroes — английское
        if source in ("manacost_public_api", "hsreplay", "hs_data_api"):
            cls_ru = cls_raw
        else:
            cls_ru = _EN_TO_RU.get(cls_raw, _EN_TO_RU.get(cls_raw.lower(), cls_raw.capitalize()))

        emoji_id = CLASS_EMOJI_ID_MAP.get(cls_ru, "")
        cls_icon = f'<tg-emoji emoji-id="{emoji_id}">🛡️</tg-emoji>' if emoji_id else "🃏"

        wr    = get_wr(s)
        count = f'{s.get("totalGames", 0):,}'.replace(",", "\u202f")

        rank = _MEDALS.get(i, f"{i:2}.")
        bar  = _bar(wr)

        # Показываем 7+ только когда источник действительно прислал метрику.
        extra = ""
        seven_plus = s.get("_pct_7plus")
        if (
            source in ("manacost_public_api", "hsreplay", "hs_data_api")
            and isinstance(seven_plus, (int, float))
        ):
            extra = f"  🏆 <b>7+:</b> {seven_plus:.1f}%"

        lines.append(f"{rank} {cls_icon} <b>{cls_ru}</b>")
        lines.append(f"   <code>{bar}</code> <b>{wr:.1f}%</b>  <i>{count} {count_label}</i>{extra}")

    lines.append("──────────────────────")
    if any(isinstance(s.get("_pct_7plus"), (int, float)) for s in stats):
        lines.append("🏆 <b>7+</b> — доля ранов с 7 и более победами")
    lines.append(f"<i>Обновляется раз в час · источник: {source_label}</i>")
    return "\n".join(lines)


def format_arena_matrix_message(data: dict) -> str:
    """Builds an HTML message with HSReplay dual-class arena winrate matrix."""
    stats = sorted(data.get("stats", []), key=lambda item: float(item.get("_win_rate") or 0), reverse=True)
    matchups = data.get("matchups", [])
    if not matchups:
        raise ValueError("нет данных матрицы")

    class_order = [s["playerClass"] for s in stats if s.get("playerClass") in _RU_TO_CODE]
    if not class_order:
        class_order = sorted(
            {m["class_a"] for m in matchups if m.get("class_a") in _RU_TO_CODE},
            key=lambda cls: _RU_TO_CODE[cls],
        )

    lookup = {
        (m["class_a"], m["class_b"]): float(m.get("win_rate") or 0)
        for m in matchups
        if m.get("class_a") in _RU_TO_CODE and m.get("class_b") in _RU_TO_CODE
    }

    header = "     " + "".join(f"{_RU_TO_CODE[cls]:>5}" for cls in class_order)
    matrix_lines = [header]
    for row_cls in class_order:
        row = [f"{_RU_TO_CODE[row_cls]:<4}"]
        for col_cls in class_order:
            wr = lookup.get((row_cls, col_cls))
            row.append(f"{wr:5.1f}" if wr else f"{'--':>5}")
        matrix_lines.append("".join(row))

    top_pairs = sorted(
        (m for m in matchups if m.get("class_a") in _RU_TO_CODE and m.get("class_b") in _RU_TO_CODE),
        key=lambda item: float(item.get("win_rate") or 0),
        reverse=True,
    )[:5]

    arena_icon = '<tg-emoji emoji-id="5327887994177225636">🏟️</tg-emoji>'
    lines = [
        f"{arena_icon} <b>Арена — Матрица классов</b>",
        "📅 Актуально · HSReplay · значения в %",
        "<i>Строка = основной класс, столбец = второй класс/геройская сила.</i>\n",
        f"<pre>{chr(10).join(matrix_lines)}</pre>",
        "──────────────────────",
        "<b>Топ пары</b>",
    ]

    for i, item in enumerate(top_pairs, 1):
        lines.append(
            f"{_MEDALS.get(i, str(i) + '.')} <b>{item['class_a']} + {item['class_b']}</b> — "
            f"{float(item.get('win_rate') or 0):.1f}%"
        )

    lines += [
        "──────────────────────",
        "<i>" + " · ".join(_CODE_LEGEND) + "</i>",
    ]
    return "\n".join(lines)


def _format_matrix_value(value: float | None) -> str:
    """Inline-only rich HTML for a matrix cell."""
    if not value:
        return "--"
    text = f"{value:.1f}"
    if value >= 55:
        return f"<mark><b>{text}</b></mark>"
    if value >= 52:
        return f"<b>{text}</b>"
    if value < 40:
        return f"<i>{text}</i>"
    return text


def format_arena_matrix_rich_html(data: dict) -> str:
    """Builds a Bot API 10.1 rich HTML table for the HSReplay arena matrix."""
    stats = sorted(data.get("stats", []), key=lambda item: float(item.get("_win_rate") or 0), reverse=True)
    matchups = data.get("matchups", [])
    if not matchups:
        raise ValueError("нет данных матрицы")

    class_order = [s["playerClass"] for s in stats if s.get("playerClass") in _RU_TO_CODE]
    if not class_order:
        class_order = sorted(
            {m["class_a"] for m in matchups if m.get("class_a") in _RU_TO_CODE},
            key=lambda cls: _RU_TO_CODE[cls],
        )

    lookup = {
        (m["class_a"], m["class_b"]): float(m.get("win_rate") or 0)
        for m in matchups
        if m.get("class_a") in _RU_TO_CODE and m.get("class_b") in _RU_TO_CODE
    }
    top_pairs = sorted(
        (m for m in matchups if m.get("class_a") in _RU_TO_CODE and m.get("class_b") in _RU_TO_CODE),
        key=lambda item: float(item.get("win_rate") or 0),
        reverse=True,
    )[:5]

    rows = [
        "<tr><th>Класс</th>"
        + "".join(f"<th>{html.escape(_RU_TO_CODE[cls])}</th>" for cls in class_order)
        + "</tr>"
    ]
    for row_cls in class_order:
        row_cells = [f"<td><b>{html.escape(_RU_TO_CODE[row_cls])}</b></td>"]
        for col_cls in class_order:
            row_cells.append(f"<td align=\"right\">{_format_matrix_value(lookup.get((row_cls, col_cls)))}</td>")
        rows.append("<tr>" + "".join(row_cells) + "</tr>")

    top_items = []
    for i, item in enumerate(top_pairs, 1):
        class_a = html.escape(item["class_a"])
        class_b = html.escape(item["class_b"])
        win_rate = float(item.get("win_rate") or 0)
        top_items.append(
            f"<li>{html.escape(_MEDALS.get(i, str(i) + '.'))} "
            f"<b>{class_a} + {class_b}</b> — {win_rate:.1f}%</li>"
        )

    legend_items = "".join(f"<li><code>{html.escape(item)}</code></li>" for item in _CODE_LEGEND)
    return "\n".join(
        [
            "<h2>🏟️ Арена — Матрица классов</h2>",
            "<p><b>Актуально · HSReplay · значения в %</b></p>",
            "<p><i>Строка = основной класс, столбец = второй класс/геройская сила.</i></p>",
            "<table bordered striped>",
            "<caption>Винрейт пары классов</caption>",
            *rows,
            "</table>",
            "<h3>Топ пары</h3>",
            "<ol>" + "".join(top_items) + "</ol>",
            "<details><summary>Сокращения классов</summary><ul>" + legend_items + "</ul></details>",
        ]
    )
