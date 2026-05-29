"""
Configuration for the Deckview bot, HTTP API and HSGuru import pipeline.
All secrets are read from environment variables or .env; none are stored in code.
"""
from __future__ import annotations

import os
import sys as _sys
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default) or default)
    except ValueError:
        return int(default)


def _env_float(name: str, default: str, minimum: float | None = None) -> float:
    try:
        value = float((os.getenv(name, default) or default).replace(",", "."))
    except ValueError:
        value = float(default)
    if minimum is not None:
        return max(minimum, value)
    return value


# Telegram bot token. Historical code uses BOT_TOKEN, newer Deckview code also
# accepts TOKEN, so keep both names in sync.
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TOKEN") or "").strip()
TOKEN = BOT_TOKEN
BATTLE_NET_TOKEN = os.getenv("BATTLE_NET_TOKEN")

# Card data and assets.
FOLDER = "cards/"
IMAGES_PATH = Path(os.getenv("IMAGES_PATH", "cards"))
if not IMAGES_PATH.exists():
    fallback_cards = Path("cards")
    fallback_legacy = Path("cards_images")
    if fallback_cards.exists():
        IMAGES_PATH = fallback_cards
    elif fallback_legacy.exists():
        IMAGES_PATH = fallback_legacy

JSON_PATH = Path(os.getenv("JSON_PATH", "cards.json"))
JSON_RU_PATH = Path(os.getenv("JSON_RU_PATH", "cardsRU.json"))

CARDS_PER_ROW = _env_int("CARDS_PER_ROW", "5")
CARD_WIDTH = _env_int("CARD_WIDTH", "200")
CARD_HEIGHT = _env_int("CARD_HEIGHT", "300")

# Blizzard Hearthstone API (optional).
BLIZZARD_ENABLED = _env_bool("BLIZZARD_ENABLED", "0")
BLIZZARD_CLIENT_ID = os.getenv("BLIZZARD_CLIENT_ID", "")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET", "")
BLIZZARD_REGION = os.getenv("BLIZZARD_REGION", "eu")
BLIZZARD_LOCALE = os.getenv("BLIZZARD_LOCALE", "en_US")
BLIZZARD_LOCALE_RU = os.getenv("BLIZZARD_LOCALE_RU", "ru_RU")
BLIZZARD_CACHE_DIR = Path(os.getenv("BLIZZARD_CACHE_DIR", "cache/blizzard"))
BLIZZARD_CACHE_TTL_HOURS = _env_int("BLIZZARD_CACHE_TTL_HOURS", "24")
BLIZZARD_IMAGE_CACHE_DIR = Path(os.getenv("BLIZZARD_IMAGE_CACHE_DIR", "cache/blizzard_images"))
BLIZZARD_COLLECTIBLE_ONLY = _env_bool("BLIZZARD_COLLECTIBLE_ONLY", "0")

# Telegram channel publishing.
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_BOT_TOKEN = os.getenv("CHANNEL_BOT_TOKEN", "").strip() or BOT_TOKEN
_discussion = os.getenv("DISCUSSION_GROUP_ID", "").strip()
try:
    DISCUSSION_GROUP_ID = int(_discussion) if _discussion else None
except ValueError:
    DISCUSSION_GROUP_ID = None

_admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()]

# WordPress integration.
WP_BASE_URL = os.getenv("WP_BASE_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
WP_UPLOAD_ENABLED = _env_bool("WP_UPLOAD_ENABLED", "1")
# When enabled, WordPress receives the deck code and lets the Kolodahs sync build
# or sideload the final image, so the importer does not upload a generated PNG.
WP_USE_KOLODAHS_IMAGE = _env_bool("WP_USE_KOLODAHS_IMAGE", "1")

# HTTP API keys. API_KEY is the legacy FastAPI name; API_TOKEN is used by the
# Blizzcore /deckview-api/v1 endpoints and browser bridge.
API_KEY = (os.getenv("API_KEY", "") or os.getenv("API_TOKEN", "")).strip()
API_TOKEN = (os.getenv("API_TOKEN", "") or API_KEY).strip() or None
PUBLIC_API_AUTH_REQUIRED = _env_bool("PUBLIC_API_AUTH_REQUIRED", "0")

# Local image export.
IMAGE_EXPORT_ENABLED = _env_bool("IMAGE_EXPORT_ENABLED", "0")
IMAGE_EXPORT_DIR = Path(os.getenv("IMAGE_EXPORT_DIR", "exported_decks"))

# HSGuru parser and browser bridge.
HSGURU_ENABLED = _env_bool("HSGURU_ENABLED", "0")
HSGURU_URL = os.getenv("HSGURU_URL", "https://www.hsguru.com/streamer-decks").strip()
HSGURU_FALLBACK_URLS = tuple(
    url.strip()
    for url in os.getenv("HSGURU_FALLBACK_URLS", "https://api.hsguru.com/").split(",")
    if url.strip()
)
HSGURU_USER_AGENT = os.getenv(
    "HSGURU_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
).strip()
HSGURU_COOKIES = os.getenv("HSGURU_COOKIES", "").strip()
HSGURU_CF_CLEARANCE = os.getenv("HSGURU_CF_CLEARANCE", "").strip()
HSGURU_PROXY_URLS = tuple(
    proxy.strip()
    for proxy in os.getenv("HSGURU_PROXY_URLS", "").split(",")
    if proxy.strip()
)
HSGURU_META_URL = os.getenv("HSGURU_META_URL", "https://www.hsguru.com/meta").strip()
HSGURU_SEEN_PATH = Path(os.getenv("HSGURU_SEEN_PATH", "cache/hsguru_seen.json"))
HSGURU_INTERVAL_SECONDS = _env_int("HSGURU_INTERVAL_SECONDS", "1800")
HSGURU_PUBLISH_BATCH_LIMIT = max(0, _env_int("HSGURU_PUBLISH_BATCH_LIMIT", "0"))
HSGURU_FETCH_TIMEOUT = _env_float("HSGURU_FETCH_TIMEOUT", "30", minimum=5)
HSGURU_BROWSER_FALLBACK = _env_bool("HSGURU_BROWSER_FALLBACK", "1")
HSGURU_BROWSER_PATH = os.getenv(
    "HSGURU_BROWSER_PATH",
    os.getenv("HSGURU_ARCHETYPE_BROWSER_PATH", "/usr/bin/chromium"),
).strip()
HSGURU_BROWSER_TIMEOUT = _env_float("HSGURU_BROWSER_TIMEOUT", "35", minimum=10)

HSGURU_ARCHETYPE_API_URL = os.getenv(
    "HSGURU_ARCHETYPE_API_URL",
    "https://api.hsguru.com/api/deck-info",
).rstrip("/")
HSGURU_ARCHETYPE_CACHE_PATH = Path(
    os.getenv("HSGURU_ARCHETYPE_CACHE_PATH", "cache/hsguru_archetype_cache.json")
)
HSGURU_ARCHETYPE_CACHE_HOURS = _env_float("HSGURU_ARCHETYPE_CACHE_HOURS", "168", minimum=0)
HSGURU_ARCHETYPE_TIMEOUT = _env_float("HSGURU_ARCHETYPE_TIMEOUT", "20", minimum=1)
HSGURU_ARCHETYPE_BROWSER_FALLBACK = _env_bool("HSGURU_ARCHETYPE_BROWSER_FALLBACK", "1")
HSGURU_ARCHETYPE_BROWSER_PATH = os.getenv("HSGURU_ARCHETYPE_BROWSER_PATH", "/usr/bin/chromium").strip()
HSGURU_ARCHETYPE_BROWSER_TIMEOUT = _env_float("HSGURU_ARCHETYPE_BROWSER_TIMEOUT", "25", minimum=1)

# Published Google Sheet with archetype translations. This is the source used
# before HSGuru payloads are published to Telegram or WordPress.
ARCHETYPES_SHEET_URL = os.getenv(
    "ARCHETYPES_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRGMOTwzxCfcpQtX9jW9wVhrkqQIyU42ooWwhPaaOWy76XUes4ymwrshWs0ak_FlqGAm8g76Gluty4m/pubhtml",
).strip()

PUBLISH_INTERVAL_HOURS = _env_float("PUBLISH_INTERVAL_HOURS", "2", minimum=0)

# HearthstoneJSON import settings.
HSJSON_BUILD = os.getenv("HSJSON_BUILD", "190920").strip()
HSJSON_LOCALE = os.getenv("HSJSON_LOCALE", "ruRU").strip()
HSJSON_CARDS_URL = (
    os.getenv("HSJSON_CARDS_URL", "").strip()
    or f"https://api.hearthstonejson.com/v1/{HSJSON_BUILD}/{HSJSON_LOCALE}/cards.json"
)

# Flask web app / dashboard settings.
WEB_DATABASE_PATH = os.getenv("WEB_DATABASE_PATH", "cache/deckview_web.db")
WEB_CACHE_MAX_AGE_HOURS = _env_float("WEB_CACHE_MAX_AGE_HOURS", "24", minimum=0)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = _env_int("WEB_PORT", "5000")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "").strip() or None

# Telegram Premium Emoji IDs used in publication captions.
PREMIUM_EMOJI_ID = "5440749199161322936"

CLASS_EMOJI_ID_MAP: dict[str, str] = {
    "Воин": "5438455068149966917",
    "Охотник на демонов": "5438367558191313225",
    "Рыцарь Смерти": "5440741171867446879",
    "Охотник": "5438553208152681635",
    "Друид": "5440388387548719145",
    "Паладин": "5440845402133783633",
    "Маг": "5438158414758831946",
    "Жрец": "5440877395345173758",
    "Разбойник": "5440596933980742987",
    "Шаман": "5438410142792052203",
    "Чернокнижник": "5440711755636436510",
}

MODE_EMOJI_ID_MAP: dict[str, str] = {
    "Стандарт": "5195044355064220820",
    "Вольный": "5197162946467219199",
}


def normalize_deck_class_name(deck_class: str | None) -> str | None:
    """Return canonical Russian deck class spelling used by captions and terms."""
    if not deck_class:
        return None
    value = str(deck_class).strip()
    if value.lower() == "рыцарь смерти":
        return "Рыцарь Смерти"
    return value


def build_deck_caption(deck_class: str | None, deck_mode: str | None, cost: int) -> str:
    """Build an HTML caption with class, mode and dust cost."""
    normalized_class = normalize_deck_class_name(deck_class)
    class_emoji = CLASS_EMOJI_ID_MAP.get(normalized_class or "")
    mode_text = deck_mode or "Стандарт"
    mode_emoji = MODE_EMOJI_ID_MAP.get(mode_text)

    class_line = (
        f'<tg-emoji emoji-id="{class_emoji}">🛡️</tg-emoji> <b>Класс:</b> {normalized_class}'
        if normalized_class and class_emoji
        else f"<b>Класс:</b> {normalized_class or 'Неизвестно'}"
    )
    mode_line = (
        f'<tg-emoji emoji-id="{mode_emoji}">🎮</tg-emoji> <b>Режим:</b> {mode_text}'
        if mode_emoji
        else f"<b>Режим:</b> {mode_text}"
    )
    return (
        f"{class_line}\n"
        f"{mode_line}\n"
        f'Пыль: {cost} <tg-emoji emoji-id="{PREMIUM_EMOJI_ID}">💎</tg-emoji>'
    )


if not BOT_TOKEN:
    print("[Config] WARNING: BOT_TOKEN is not set; Telegram bot startup will fail.", file=_sys.stderr)
if not ADMIN_IDS:
    print("[Config] WARNING: ADMIN_IDS is not set; admin commands are disabled.", file=_sys.stderr)
if DASHBOARD_SECRET and len(DASHBOARD_SECRET) < 8:
    print("[Config] WARNING: DASHBOARD_SECRET is shorter than 8 chars.", file=_sys.stderr)
if not API_TOKEN:
    print("[Config] WARNING: API_TOKEN is not set; private publish endpoints are unavailable.", file=_sys.stderr)
elif len(API_TOKEN) < 24:
    print("[Config] WARNING: API_TOKEN is shorter than 24 chars.", file=_sys.stderr)
