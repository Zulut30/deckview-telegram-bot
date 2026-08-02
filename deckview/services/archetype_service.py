from __future__ import annotations

import html
import math
import re
import time
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from deckview.integrations.hs_data_api import get_db_decks
from deckview.integrations.hsguru_fetch import load_archetypes, translate_deck_name


_TOKEN_TTL_SEC = 6 * 3600
_TRANSLATIONS_TTL_SEC = 15 * 60
_RECENT_ARCHETYPES_TTL_SEC = 5 * 60
_MAX_ALIAS_QUERIES = 8

_token_cache: dict[str, tuple[float, str, Any]] = {}
_translations_cache: tuple[float, dict[str, str]] | None = None
_recent_archetypes_cache: tuple[float, list[str]] | None = None

_SOURCE_LABELS = {
    "vicious_syndicate_radars": "Vicious Syndicate",
    "hearthstone_decks": "Hearthstone-Decks",
    "metastats_decks": "MetaStats",
    "hsguru_streamer_decks": "HSGuru",
}
_SOURCE_PRIORITY = {
    "vicious_syndicate_radars": 0,
    "hearthstone_decks": 1,
    "metastats_decks": 2,
}
_FORMAT_LABELS = {
    "standard": "Стандарт",
    "wild": "Вольный",
    "classic": "Классика",
    "twist": "Твист",
}
_CLASS_TEXT_ICONS = {
    "Воин": "⚔️",
    "Охотник на демонов": "🟩",
    "Рыцарь Смерти": "💀",
    "Охотник": "🏹",
    "Друид": "🌿",
    "Паладин": "🛡️",
    "Маг": "🔮",
    "Жрец": "✨",
    "Разбойник": "🗡️",
    "Шаман": "⚡",
    "Чернокнижник": "😈",
}
_CLASS_EN_HINTS: tuple[tuple[str, str], ...] = (
    ("death knight", "Рыцарь Смерти"),
    ("demon hunter", "Охотник на демонов"),
    ("warlock", "Чернокнижник"),
    ("seedlock", "Чернокнижник"),
    ("discolock", "Чернокнижник"),
    ("lock", "Чернокнижник"),
    ("shaman", "Шаман"),
    ("hunter", "Охотник"),
    ("warrior", "Воин"),
    ("mage", "Маг"),
    ("rogue", "Разбойник"),
    ("paladin", "Паладин"),
    ("priest", "Жрец"),
    ("druid", "Друид"),
)
_CLASS_RU_HINTS: tuple[tuple[str, str], ...] = (
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
_RU_QUERY_ALIASES = (
    ("хант", "охотник"),
    ("дк", "рыцарь смерти"),
    ("дх", "охотник на демонов"),
    ("лок", "чернокнижник"),
    ("вар", "воин"),
    ("рога", "разбойник"),
    ("прист", "жрец"),
    ("пал", "паладин"),
    ("маг", "маг"),
)
_EN_QUERY_ALIASES = (
    ("hunt", "hunter"),
    ("dk", "death knight"),
    ("dh", "demon hunter"),
    ("lock", "warlock"),
    ("rog", "rogue"),
    ("priest", "priest"),
    ("pal", "paladin"),
)


def _cleanup_tokens(now: float | None = None) -> None:
    now = now or time.monotonic()
    expired = [token for token, (created, _, _) in _token_cache.items() if now - created > _TOKEN_TTL_SEC]
    for token in expired:
        _token_cache.pop(token, None)


def _make_token(kind: str, value: Any) -> str:
    now = time.monotonic()
    _cleanup_tokens(now)
    while True:
        token = uuid.uuid4().hex[:12]
        if token not in _token_cache:
            _token_cache[token] = (now, kind, value)
            return token


def _get_token(token: str, kind: str) -> Any:
    token = (token or "").strip()
    if not token:
        return None
    _cleanup_tokens()
    cached = _token_cache.get(token)
    if not cached:
        return None
    _, cached_kind, value = cached
    if cached_kind != kind:
        return None
    return value


def make_archetype_token(name_en: str) -> str:
    return _make_token("archetype", name_en)


def get_archetype_token(token: str) -> str | None:
    value = _get_token(token, "archetype")
    return str(value) if value else None


def make_deck_token(deck: dict[str, Any]) -> str:
    return _make_token("deck", deck)


def get_deck_token(token: str) -> dict[str, Any] | None:
    value = _get_token(token, "deck")
    return value if isinstance(value, dict) else None


def _translations() -> dict[str, str]:
    global _translations_cache
    now = time.monotonic()
    if _translations_cache and now - _translations_cache[0] < _TRANSLATIONS_TTL_SEC:
        return _translations_cache[1]
    data = load_archetypes()
    clean = {
        str(name_en).strip().lower(): str(name_ru).strip()
        for name_en, name_ru in data.items()
        if str(name_en).strip() and str(name_ru).strip()
    }
    _translations_cache = (now, clean)
    return clean


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_en(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text if any(ch.isupper() for ch in text) else text.title()


def _display_ru(name_en: str, translations: dict[str, str] | None = None) -> str:
    translations = translations or _translations()
    name = str(name_en or "").strip()
    if not name:
        return ""
    translated = translate_deck_name(name, translations)
    return translated if translated and translated.lower() != name.lower() else _title_en(name)


def _infer_class_name(*values: object) -> str:
    text = _normalize_text(" ".join(str(value or "") for value in values))
    if not text:
        return ""
    padded = f" {text} "
    for hint, class_name in _CLASS_EN_HINTS:
        hint_norm = _normalize_text(hint)
        if re.search(rf"\b{re.escape(hint_norm)}\b", padded):
            return class_name
    for hint, class_name in _CLASS_RU_HINTS:
        if _normalize_text(hint) in text:
            return class_name
    return ""


def _class_icon(class_name: str) -> str:
    return _CLASS_TEXT_ICONS.get(class_name, "🧬")


def _query_variants(query: str) -> set[str]:
    base = _normalize_text(query)
    variants = {base} if base else set()
    for source, target in (*_RU_QUERY_ALIASES, *_EN_QUERY_ALIASES):
        source_norm = _normalize_text(source)
        target_norm = _normalize_text(target)
        if source_norm and re.search(rf"\b{re.escape(source_norm)}\b", base):
            variants.add(re.sub(rf"\b{re.escape(source_norm)}\b", target_norm, base).strip())
    return {variant for variant in variants if variant}


def _recent_archetypes() -> list[str]:
    global _recent_archetypes_cache
    now = time.monotonic()
    if _recent_archetypes_cache and now - _recent_archetypes_cache[0] < _RECENT_ARCHETYPES_TTL_SEC:
        return _recent_archetypes_cache[1]

    seen: set[str] = set()
    names: list[str] = []
    try:
        rows = get_db_decks(limit=200)
    except Exception:
        rows = []

    for row in rows:
        name = str(row.get("archetype") or row.get("archetype_name") or "").strip()
        code = str(row.get("deck_code") or row.get("code") or "").strip()
        norm = _normalize_text(name)
        if not name or not code or norm in seen:
            continue
        seen.add(norm)
        names.append(_title_en(name))

    _recent_archetypes_cache = (now, names)
    return names


def _candidate_names(translations: dict[str, str] | None = None) -> list[str]:
    translations = translations or _translations()
    seen: set[str] = set()
    names: list[str] = []
    for name in _recent_archetypes():
        norm = _normalize_text(name)
        if norm and norm not in seen:
            seen.add(norm)
            names.append(name)
    for name in translations:
        norm = _normalize_text(name)
        if norm and norm not in seen:
            seen.add(norm)
            names.append(_title_en(name))
    return names


def _aliases_for_archetype(name_en: str, translations: dict[str, str] | None = None) -> list[str]:
    translations = translations or _translations()
    name_en = _title_en(name_en)
    name_norm = _normalize_text(name_en)
    ru = _display_ru(name_en, translations)
    ru_norm = _normalize_text(ru)

    aliases: list[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip()
        norm = _normalize_text(text)
        if norm and all(_normalize_text(existing) != norm for existing in aliases):
            aliases.append(text)

    add(name_en)
    if name_norm in translations:
        add(translations[name_norm])
    add(ru)

    for eng, rus in translations.items():
        if _normalize_text(rus) == ru_norm:
            add(eng)

    for recent in _recent_archetypes():
        if _normalize_text(_display_ru(recent, translations)) == ru_norm:
            add(recent)

    return aliases


def _score_alias(query_variants: set[str], aliases: list[str]) -> int:
    best = 0
    for variant in query_variants:
        variant_tokens = set(variant.split())
        for alias in aliases:
            alias_norm = _normalize_text(alias)
            if not alias_norm:
                continue
            alias_tokens = set(alias_norm.split())
            if variant == alias_norm:
                best = max(best, 120)
            elif variant in alias_norm or alias_norm in variant:
                best = max(best, 96 if len(variant) >= 4 else 80)
            elif variant_tokens and alias_tokens:
                overlap = len(variant_tokens & alias_tokens)
                if overlap:
                    ratio = overlap / max(len(variant_tokens), len(alias_tokens))
                    best = max(best, int(70 * ratio))
            if len(variant) >= 4 and len(alias_norm) >= 4:
                best = max(best, int(SequenceMatcher(None, variant, alias_norm).ratio() * 82))
    return best


def resolve_archetype_query(query: str) -> dict[str, Any] | None:
    query_variants = _query_variants(query)
    if not query_variants:
        return None

    translations = _translations()
    recent_norms = {_normalize_text(name) for name in _recent_archetypes()}
    scored: list[tuple[int, int, int, dict[str, Any]]] = []

    for name_en in _candidate_names(translations):
        name_en = _title_en(name_en)
        ru = _display_ru(name_en, translations)
        own_aliases = [name_en, ru]
        sibling_aliases = [
            alias
            for alias in _aliases_for_archetype(name_en, translations)
            if _normalize_text(alias) not in {_normalize_text(name_en), _normalize_text(ru)}
        ]
        score = _score_alias(query_variants, own_aliases)
        if sibling_aliases:
            score = max(score, min(92, _score_alias(query_variants, sibling_aliases)))
        if score < 58:
            continue

        exact_own = any(
            variant in {_normalize_text(name_en), _normalize_text(ru)}
            for variant in query_variants
        )
        priority = 0 if exact_own else 1
        if _normalize_text(name_en) in recent_norms:
            priority -= 1
        scored.append((
            score,
            -priority,
            -len(_normalize_text(name_en)),
            {
                "name_en": name_en,
                "name_ru": ru,
                "score": score,
                "aliases": _aliases_for_archetype(name_en, translations),
            },
        ))

    if not scored:
        return None

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return scored[0][3]


def list_archetypes(page: int = 0, per_page: int = 12) -> dict[str, Any]:
    translations = _translations()
    names = _recent_archetypes() or _candidate_names(translations)
    names.sort(key=lambda name: (_normalize_text(_display_ru(name, translations)), _normalize_text(name)))

    total = len(names)
    total_pages = max(1, math.ceil(total / max(1, per_page)))
    page = max(0, min(int(page or 0), total_pages - 1))
    start = page * per_page
    items = []
    for name in names[start:start + per_page]:
        name_en = _title_en(name)
        name_ru = _display_ru(name_en, translations)
        class_name = _infer_class_name(name_en, name_ru)
        items.append({
            "name_en": name_en,
            "name_ru": name_ru,
            "class_name": class_name,
            "class_icon": _class_icon(class_name),
        })
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


def _row_text(row: dict[str, Any]) -> str:
    fields = (
        "archetype",
        "archetype_name",
        "deck_name",
        "name",
        "title",
        "player",
        "streamer",
    )
    return " ".join(str(row.get(field) or "") for field in fields)


def _row_matches_archetype(row: dict[str, Any], name_en: str, aliases: list[str], translations: dict[str, str]) -> bool:
    alias_norms = {
        _normalize_text(alias)
        for alias in aliases
        if len(_normalize_text(alias)) >= 4
    }
    if not alias_norms:
        return False

    row_arch = str(row.get("archetype") or row.get("archetype_name") or "").strip()
    row_arch_norm = _normalize_text(row_arch)
    if row_arch_norm in alias_norms:
        return True

    target_ru_norm = _normalize_text(_display_ru(name_en, translations))
    if row_arch and _normalize_text(_display_ru(row_arch, translations)) == target_ru_norm:
        return True

    haystack = _normalize_text(_row_text(row))
    return any(alias_norm in haystack for alias_norm in alias_norms)


def _parse_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _format_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%d.%m")
    except Exception:
        return text[:10]


def _format_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _FORMAT_LABELS.get(text.lower(), text)


def _source_label(source_id: Any) -> str:
    source = str(source_id or "").strip()
    return _SOURCE_LABELS.get(source, source.replace("_", " ").title() if source else "")


def _deck_name(row: dict[str, Any], translations: dict[str, str]) -> str:
    title = str(row.get("title") or row.get("deck_name") or row.get("name") or "").strip()
    if title:
        return re.sub(r"\s+", " ", title)
    archetype = str(row.get("archetype") or row.get("archetype_name") or "").strip()
    return _display_ru(archetype, translations) if archetype else "Колода"


def _normalize_deck(row: dict[str, Any], translations: dict[str, str]) -> dict[str, Any] | None:
    code = str(row.get("deck_code") or row.get("code") or "").strip()
    if not code:
        return None
    name = _deck_name(row, translations)
    deck_format = _format_label(row.get("format") or row.get("format_name") or "")
    source = _source_label(row.get("source_id"))
    score = str(row.get("score") or row.get("winrate") or "").strip()
    games = str(row.get("games") or row.get("total_games") or "").strip()
    updated = _format_date(row.get("updated_at") or row.get("source_fetched_at") or "")

    parts = [part for part in (deck_format, source, score or games, f"обн. {updated}" if updated else "") if part]
    button = name
    if len(button) > 54:
        button = button[:51].rstrip() + "..."

    return {
        "deck_code": code,
        "deck_name": name,
        "button_label": button,
        "format": deck_format,
        "source": source,
        "score": score,
        "games": games,
        "updated": updated,
        "meta": " · ".join(parts),
        "source_id": row.get("source_id"),
        "updated_at": row.get("updated_at"),
        "_sort_ts": _parse_timestamp(row.get("updated_at") or row.get("source_fetched_at")),
    }


def get_archetype_decks(name_en: str, page: int = 0, per_page: int = 8) -> dict[str, Any]:
    translations = _translations()
    name_en = _title_en(name_en)
    aliases = _aliases_for_archetype(name_en, translations)
    search_aliases = [
        alias
        for alias in aliases
        if re.search(r"[a-zA-Z]", alias)
    ][:_MAX_ALIAS_QUERIES]
    if name_en not in search_aliases:
        search_aliases.insert(0, name_en)

    seen_codes: set[str] = set()
    decks: list[dict[str, Any]] = []
    for alias in search_aliases:
        try:
            rows = get_db_decks(q=alias, limit=200)
        except Exception:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("deck_code") or row.get("code") or "").strip()
            if not code or code in seen_codes:
                continue
            if not _row_matches_archetype(row, name_en, aliases, translations):
                continue
            deck = _normalize_deck(row, translations)
            if not deck:
                continue
            seen_codes.add(code)
            decks.append(deck)

    decks.sort(key=lambda deck: (
        deck.get("_sort_ts") or 0,
        -_SOURCE_PRIORITY.get(str(deck.get("source_id") or ""), 99),
    ), reverse=True)

    total = len(decks)
    total_pages = max(1, math.ceil(total / max(1, per_page)))
    page = max(0, min(int(page or 0), total_pages - 1))
    start = page * per_page
    page_decks = [{k: v for k, v in deck.items() if not k.startswith("_")} for deck in decks[start:start + per_page]]

    name_ru = _display_ru(name_en, translations)
    class_name = _infer_class_name(name_en, name_ru)

    return {
        "archetype": {
            "name_en": name_en,
            "name_ru": name_ru,
            "class_name": class_name,
            "class_icon": _class_icon(class_name),
            "aliases": aliases,
        },
        "decks": page_decks,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


def format_archetype_list_message(data: dict[str, Any]) -> str:
    page = int(data.get("page") or 0)
    total_pages = int(data.get("total_pages") or 1)
    total = int(data.get("total") or 0)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    per_page = int(data.get("per_page") or 12)

    lines = [
        "🧬 <b>Архетипы Hearthstone</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📚 <b>{total}</b> архетипов с актуальными колодами",
        f"📄 Страница <b>{page + 1}</b> из <b>{total_pages}</b>",
        "",
        "Выберите архетип кнопкой ниже или напишите название:",
        "<code>/archetip Фейс Хант</code>",
        "<code>/archetip Face Hunter</code>",
        "",
    ]
    if items:
        lines.append("<b>На этой странице:</b>")
    for index, item in enumerate(items, start=page * per_page + 1):
        name_ru = html.escape(str(item.get("name_ru") or item.get("name_en") or "Архетип"))
        name_en = html.escape(str(item.get("name_en") or ""))
        icon = html.escape(str(item.get("class_icon") or "🧬"))
        if name_en and name_en.lower() != name_ru.lower():
            lines.append(f"{index}. {icon} <b>{name_ru}</b> · <i>{name_en}</i>")
        else:
            lines.append(f"{index}. {icon} <b>{name_ru}</b>")
    return "\n".join(lines).strip()


def format_archetype_decks_message(data: dict[str, Any]) -> str:
    archetype = data.get("archetype") if isinstance(data.get("archetype"), dict) else {}
    name_ru = html.escape(str(archetype.get("name_ru") or archetype.get("name_en") or "Архетип"))
    name_en = html.escape(str(archetype.get("name_en") or ""))
    class_icon = html.escape(str(archetype.get("class_icon") or "🧬"))
    total = int(data.get("total") or 0)
    page = int(data.get("page") or 0)
    total_pages = int(data.get("total_pages") or 1)
    per_page = int(data.get("per_page") or 8)
    decks = data.get("decks") if isinstance(data.get("decks"), list) else []

    header = f"{class_icon} <b>{name_ru}</b>"
    if name_en and name_en.lower() != name_ru.lower():
        header += f"\n<i>{name_en}</i>"

    if not total:
        return "\n".join([
            header,
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            "Колод с кодом для этого архетипа пока не нашёл в API.",
        ])

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━━━",
        f"🃏 Колод с кодом: <b>{total}</b>",
        f"📄 Страница <b>{page + 1}</b> из <b>{total_pages}</b>",
        "",
    ]
    for index, deck in enumerate(decks, start=page * per_page + 1):
        name = html.escape(str(deck.get("deck_name") or f"Колода {index}"))
        meta = html.escape(str(deck.get("meta") or ""))
        lines.append(f"{index}. {class_icon} <b>{name}</b>")
        if meta:
            lines.append(f"   <i>{meta}</i>")
    lines.append("")
    lines.append("Нажмите на колоду ниже, чтобы получить картинку и код.")
    return "\n".join(lines).strip()
