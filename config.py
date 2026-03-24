"""
Конфигурация бота - загрузка переменных окружения.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()
    
# Токен бота из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Путь к папке с изображениями карт
IMAGES_PATH = Path(os.getenv("IMAGES_PATH", "cards"))
if not IMAGES_PATH.exists():
    fallback_cards = Path("cards")
    fallback_legacy = Path("cards_images")
    if fallback_cards.exists():
        IMAGES_PATH = fallback_cards
    elif fallback_legacy.exists():
        IMAGES_PATH = fallback_legacy

# Путь к файлу cards.json
JSON_PATH = Path(os.getenv("JSON_PATH", "cards.json"))

# Путь к файлу cardsRU.json с русскими названиями
JSON_RU_PATH = Path(os.getenv("JSON_RU_PATH", "cardsRU.json"))

# Blizzard Hearthstone API (опционально)
BLIZZARD_ENABLED = os.getenv("BLIZZARD_ENABLED", "0") == "1"
BLIZZARD_CLIENT_ID = os.getenv("BLIZZARD_CLIENT_ID", "")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET", "")
BLIZZARD_REGION = os.getenv("BLIZZARD_REGION", "eu")
BLIZZARD_LOCALE = os.getenv("BLIZZARD_LOCALE", "en_US")
BLIZZARD_LOCALE_RU = os.getenv("BLIZZARD_LOCALE_RU", "ru_RU")
BLIZZARD_CACHE_DIR = Path(os.getenv("BLIZZARD_CACHE_DIR", "cache/blizzard"))
BLIZZARD_CACHE_TTL_HOURS = int(os.getenv("BLIZZARD_CACHE_TTL_HOURS", "24"))
BLIZZARD_IMAGE_CACHE_DIR = Path(os.getenv("BLIZZARD_IMAGE_CACHE_DIR", "cache/blizzard_images"))
BLIZZARD_COLLECTIBLE_ONLY = os.getenv("BLIZZARD_COLLECTIBLE_ONLY", "0") == "1"

# WordPress интеграция (опционально)
WP_BASE_URL = os.getenv("WP_BASE_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
WP_UPLOAD_ENABLED = os.getenv("WP_UPLOAD_ENABLED", "1") == "1"

# API ключ для собственного HTTP API (опционально)
API_KEY = os.getenv("API_KEY", "")

# Экспорт изображений на сервер (локальная папка)
IMAGE_EXPORT_ENABLED = os.getenv("IMAGE_EXPORT_ENABLED", "0") == "1"
IMAGE_EXPORT_DIR = Path(os.getenv("IMAGE_EXPORT_DIR", "exported_decks"))

# HSGuru парсер (внутри бота)
HSGURU_ENABLED = os.getenv("HSGURU_ENABLED", "0") == "1"
HSGURU_URL = os.getenv("HSGURU_URL", "https://www.hsguru.com/streamer-decks")
HSGURU_SEEN_PATH = Path(os.getenv("HSGURU_SEEN_PATH", "cache/hsguru_seen.json"))
# DEPRECATED: Используется фиксированный интервал 30 минут в hsguru_scraper.py, параметр оставлен для обратной совместимости
HSGURU_INTERVAL_SECONDS = int(os.getenv("HSGURU_INTERVAL_SECONDS", "1800"))  # 30 минут (1800 секунд)

# Количество карт в одной строке сетки
CARDS_PER_ROW = int(os.getenv("CARDS_PER_ROW", "5"))

# Размер одной карты в пикселях (ширина x высота)
CARD_WIDTH = int(os.getenv("CARD_WIDTH", "200"))
CARD_HEIGHT = int(os.getenv("CARD_HEIGHT", "300"))

# ID администраторов бота (через запятую в .env)
# Пример: ADMIN_IDS=123456789,987654321
_admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()]

# ID Telegram канала для публикации колод (опционально)
# Можно указать как числовой ID (например: -1002913200752) или username (например: @dcboom_hs)
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

# Проверка наличия обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения!")


