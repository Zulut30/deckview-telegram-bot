"""
Battlegrounds — лучшие стратегии (компы).

Источник: zerotoheroes.com / firestoneapp.com
API: https://static.zerotoheroes.com/api/bgs/comp-stats/{period}/overview-from-hourly.gz.json
"""

import time

from framework.http_session import get_http_session

# ─── Константы ────────────────────────────────────────────────────────────────

_API_URL = (
    "https://static.zerotoheroes.com/api/bgs/comp-stats/{period}/overview-from-hourly.gz.json"
)

CACHE_TTL = 3600  # 1 час
MIN_GAMES = 500   # минимальный порог партий для отображения

PERIOD_LABEL: dict[str, str] = {
    "last-patch": "Патч",
    "past-seven": "7 дней",
    "past-three": "3 дня",
}

# ─── Tribe emoji IDs ───────────────────────────────────────────────────────────

TRIBE_EMOJI: dict[str, tuple[str, str]] = {
    "quilboar":   ("5427108185460740087", "🐗"),
    "elemental":  ("5427263276729797208", "🌊"),
    "murloc":     ("5427062023152241311", "🐟"),
    "dragon":     ("5427144748517328946", "🐉"),
    "mech":       ("5426849722918801046", "⚙️"),
    "naga":       ("5426856358643272914", "🐍"),
    "pirate":     ("5427079486489269241", "🏴‍☠️"),
    "undead":     ("5427249451230071652", "💀"),
    "demon":      ("5426998693859465904", "😈"),
    "beast":      ("5427100488879343851", "🐾"),
    "general":    ("5330013345858752043", "🎯"),
}

# ─── Маппинг архетипов → ключ трайба ─────────────────────────────────────────

ARCHETYPE_TRIBE: dict[str, str] = {
    "end_of_turn_naga":        "naga",
    "deathrattle_naga":        "naga",
    "deep_blue_nagas":         "naga",
    "apexis_mechs":            "mech",
    "bomber_mechs":            "mech",
    "shield_mechs":            "mech",
    "lord_of_ruins_demons":    "demon",
    "buff_shop_demons":        "demon",
    "damage_demons":           "demon",
    "demons_beetles":          "demon",
    "self_damage_demons":      "demon",
    "deathrattle_quilboar":    "quilboar",
    "cycle_quilboar":          "quilboar",
    "boost_shop_quilboar":     "quilboar",
    "rally_quilboar":          "quilboar",
    "quilboar - gem in fight": "quilboar",
    "beasts_beetles":          "beast",
    "silithid_beasts":         "beast",
    "self_damage_beasts":      "beast",
    "selfdamage_beasts":       "beast",
    "goldrinn_beasts":         "beast",
    "attack_undead":           "undead",
    "overflow_undead":         "undead",
    "carapace_undead":         "undead",
    "end_of_turn_murlocs":     "murloc",
    "handbuff_murloc":         "murloc",
    "stuntdrake_dragons":      "dragon",
    "whelp_dragon":            "dragon",
    "evoker_dragons":          "dragon",
    "avenge_dragons":          "dragon",
    "refresh_elementals":      "elemental",
    "nomi_elementals":         "elemental",
    "apm_pirate":              "pirate",
    "scam":                    "general",
    "tier_2_ballers":          "general",
    "spell_cycle":             "general",
    "overflow_beetles":        "general",
}

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_STAR_ICON = '<tg-emoji emoji-id="5305703886797969403">⭐</tg-emoji>'

# ─── Кэш ──────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}


def _fetch(period: str) -> dict:
    url = _API_URL.format(period=period)
    resp = get_http_session().get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0",
            "Accept-Encoding": "gzip",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_bgs_comps(period: str = "last-patch") -> dict:
    """Возвращает данные по компам из API (с кэшем 1 час).
    При ошибке запроса возвращает устаревший кэш, если он есть."""
    now = time.monotonic()
    cached = _cache.get(period)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        data = _fetch(period)
        _cache[period] = (now, data)
        return data
    except Exception as e:
        print(f"[BGS Comps] Ошибка получения данных ({period}): {e}")
        if cached:
            print(f"[BGS Comps] Используется устаревший кэш ({period})")
            return cached[1]
        raise


# ─── Форматирование ────────────────────────────────────────────────────────────

def _tribe_icon(tribe_key: str) -> str:
    emoji_id, fallback = TRIBE_EMOJI.get(tribe_key, ("", "🎯"))
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def _placement_bar(avg: float, width: int = 9) -> str:
    """Прогресс-бар: avg placement 1.0 (лучший) → 7.0 (худший)."""
    filled = round((7.0 - avg) / 6.0 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "▒" * (width - filled)


def _top4_pct(comp: dict) -> float | None:
    """Вычисляет % top-4 финишей из placementDistribution."""
    dist = comp.get("placementDistribution") or []
    if not dist:
        return None
    total = sum(d.get("totalMatches", 0) for d in dist)
    top4 = sum(d.get("totalMatches", 0) for d in dist if d.get("rank", 9) <= 4)
    if total == 0:
        return None
    return top4 / total * 100


def format_comps_message(data: dict, period: str) -> str:
    """Строит HTML-сообщение для команды /comps."""
    all_comps = data.get("compStats", [])
    total_dp = data.get("dataPoints", 0)
    total_str = f"{total_dp:,}".replace(",", "\u202f")
    period_label = PERIOD_LABEL.get(period, period)

    # Фильтруем малые выборки и сортируем по avg placement (меньше = лучше)
    comps = [
        c for c in all_comps
        if c.get("dataPoints", 0) >= MIN_GAMES
    ]
    comps.sort(key=lambda x: x.get("averagePlacement", 99))

    bgs_icon = '<tg-emoji emoji-id="5438566720119795596">🎮</tg-emoji>'
    lines = [
        f"{bgs_icon} <b>Поля сражений — Лучшие стратегии</b>",
        f"📅 {period_label} · {total_str} партий\n",
        "──────────────────────",
    ]

    for i, comp in enumerate(comps, 1):
        archetype = comp.get("archetype", "")
        display_name = archetype.replace("_", " ").replace("-", " ").title()
        tribe_key = ARCHETYPE_TRIBE.get(archetype, "general")
        tribe_icon = _tribe_icon(tribe_key)

        avg = comp["averagePlacement"]
        games = f'{comp["dataPoints"]:,}'.replace(",", "\u202f")
        bar = _placement_bar(avg)
        top4 = _top4_pct(comp)
        top4_str = f"  🏆 <b>{top4:.0f}%</b> top‑4" if top4 is not None else ""

        rank = _MEDALS.get(i, f"<b>{i}.</b>")
        lines.append(f"{rank} {tribe_icon} <b>{display_name}</b>")
        lines.append(
            f"   <code>{bar}</code> {_STAR_ICON} <b>{avg:.2f}</b> avg{top4_str}  <i>{games} игр</i>"
        )

    lines += [
        "──────────────────────",
        "<i>Обновляется раз в час · источник: firestoneapp.com</i>",
    ]
    return "\n".join(lines)
