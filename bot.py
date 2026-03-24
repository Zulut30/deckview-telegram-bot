"""
Основной файл Telegram-бота для визуализации колод Hearthstone.
Использует aiogram 3.x для обработки сообщений.
"""
import asyncio
import re
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Set
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile, FSInputFile, InlineKeyboardMarkup, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, Filter
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp
import cloudscraper
from bs4 import BeautifulSoup
import hearthstone.deckstrings
import config
from loader import CardDatabase
from generator import DeckImageGenerator
from database import DeckDatabase
from wordpress import upload_deck_image, create_hs_deck_post
import hsguru_scraper
from datetime import datetime, timedelta


# ============================================================================
# HSGuru PARSER CONSTANTS
# ============================================================================

# Время последней публикации в Telegram канал
last_telegram_channel_publish: Optional[datetime] = None

# Состояние ввода архетипов (user_id -> {"action": "add"|"edit", "step": 1|2, "eng": str})
_archetype_state: Dict[int, Dict[str, object]] = {}
# Кэш списка колод HSGuru (user_id -> {"decks": list, "page": int, "ts": datetime})
_admin_deck_cache: Dict[int, Dict[str, object]] = {}

HSGURU_URL = "https://www.hsguru.com/streamer-decks"
SEEN_DECKS_FILE = Path("seen_decks.json")

# Стоимость пыли по редкости
DUST_COSTS = {
    "COMMON": 40,
    "RARE": 100,
    "EPIC": 400,
    "LEGENDARY": 1600,
}

# Hero DBF ID to class name (Russian)
HERO_CLASS_MAP = {
    274: "Друид", 7: "Воин", 31: "Охотник", 637: "Маг",
    671: "Паладин", 813: "Жрец", 893: "Чернокнижник",
    930: "Разбойник", 1066: "Шаман", 56550: "Охотник на демонов",
    78065: "Рыцарь смерти",
    2826: "Воин", 2827: "Охотник", 2828: "Маг", 2829: "Шаман",
    40195: "Жрец", 40183: "Паладин", 40323: "Чернокнижник",
    57761: "Разбойник", 60224: "Друид", 74481: "Охотник на демонов",
}

# Format mapping
FORMAT_MAP = {
    "Standard": "Стандарт",
    "Wild": "Вольный",
    "Classic": "Классический",
    "Twist": "Потасовка",
}

# Минимальное количество игр для публикации колоды
MIN_GAMES = 20

# Special deck art override
SPECIAL_WIZBANG_DECK_NAME = "Splendiferous Whizbang"
SPECIAL_WIZBANG_ART_PATH = Path(
    "/home/ubuntu/.cursor/projects/home-ubuntu/assets/"
    "c__Users_astap_AppData_Roaming_Cursor_User_workspaceStorage_a6d83113f01fbc353e25f633df8302d7_images_"
    "Generated_Image_February_10__2026_-_2_31PM-86c4dca5-9e8f-41b6-99e2-23e80555a2bb.png"
)


def load_seen_decks() -> set:
    """Загружает список уже опубликованных колод."""
    if SEEN_DECKS_FILE.exists():
        try:
            with open(SEEN_DECKS_FILE, "r") as f:
                return set(json.load(f))
        except:
            pass
    return set()


def _is_special_wizbang_deck(deck_name: str) -> bool:
    if not deck_name:
        return False
    return deck_name.strip().lower() == SPECIAL_WIZBANG_DECK_NAME.lower()


def _maybe_override_deck_art(deck_name: str, image_bytes: BytesIO) -> BytesIO:
    if not _is_special_wizbang_deck(deck_name):
        return image_bytes
    try:
        if SPECIAL_WIZBANG_ART_PATH.is_file():
            return BytesIO(SPECIAL_WIZBANG_ART_PATH.read_bytes())
        print(f"[Art Override] ⚠ Файл арта не найден: {SPECIAL_WIZBANG_ART_PATH}")
    except Exception as e:
        print(f"[Art Override] ❌ Ошибка чтения арта: {e}")
    return image_bytes


def save_seen_decks(seen: set):
    """Сохраняет список опубликованных колод."""
    with open(SEEN_DECKS_FILE, "w") as f:
        json.dump(list(seen), f)


async def fetch_hsguru_html() -> str:
    """Получает HTML страницы HSGuru (с обходом Cloudflare)."""
    # Используем cloudscraper для обхода Cloudflare protection
    loop = asyncio.get_event_loop()
    
    def fetch_sync():
        scraper = cloudscraper.create_scraper()
        response = scraper.get(HSGURU_URL, timeout=30)
        response.raise_for_status()
        return response.text
    
    return await loop.run_in_executor(None, fetch_sync)


def parse_hsguru_decks(html: str) -> List[Dict]:
    """Парсит колоды из HTML страницы HSGuru."""
    soup = BeautifulSoup(html, "html.parser")
    decks = []
    
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        
        try:
            # Название колоды
            deck_name = ""
            deck_link = row.select_one('a[href^="/deck/"]')
            if deck_link:
                deck_name = deck_link.get_text(strip=True)
            
            # Стример
            streamer = cells[1].get_text(strip=True)
            
            # Формат
            format_cell = cells[2].get_text(strip=True)
            
            # Код колоды
            clip_elem = row.select_one("[data-clipboard-text]")
            deck_code = ""
            if clip_elem:
                deck_code = clip_elem.get("data-clipboard-text", "")
            
            if not deck_code or not deck_name:
                continue
            
            # Определяем класс колоды
            deck_class = "Unknown"
            try:
                deck_parts = hearthstone.deckstrings.parse_deckstring(deck_code)
                heroes = deck_parts[1]
                if heroes:
                    deck_class = HERO_CLASS_MAP.get(heroes[0], "Unknown")
            except:
                pass
            
            # Режим
            deck_mode = FORMAT_MAP.get(format_cell, "Стандарт")
            
            decks.append({
                "deck_name": deck_name,
                "streamer": streamer,
                "deck_code": deck_code,
                "deck_class": deck_class,
                "deck_mode": deck_mode,
                "format": format_cell,
            })
        except Exception:
            continue
    
    return decks


# Глобальные переменные
bot: Bot = None
dp: Dispatcher = None
card_db: CardDatabase = None
generator: DeckImageGenerator = None
deck_db: DeckDatabase = None

CACHE_DIR = Path("cache") / "decks"


def _deck_hash(deck_string: str) -> str:
    return hashlib.sha256(deck_string.encode("utf-8")).hexdigest()


def _cache_paths(deck_string: str) -> Tuple[Path, Path]:
    deck_hash = _deck_hash(deck_string)
    return (
        CACHE_DIR / f"{deck_hash}.png",
        CACHE_DIR / f"{deck_hash}.json",
    )


def build_vote_keyboard(message_id: int, include_counts: bool = True) -> InlineKeyboardMarkup:
    counts = {"like": 0, "dislike": 0}
    if include_counts and deck_db:
        counts = deck_db.get_vote_counts(message_id)
    builder = InlineKeyboardBuilder()
    like_text = f"👍 {counts['like']}" if include_counts else "👍"
    dislike_text = f"👎 {counts['dislike']}" if include_counts else "👎"
    builder.button(text=like_text, callback_data=f"vote:like:{message_id}")
    builder.button(text=dislike_text, callback_data=f"vote:dislike:{message_id}")
    builder.adjust(2)
    return builder.as_markup()


def build_admin_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру админ-панели."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 Проверить отправку колод", callback_data="admin:test")
    builder.button(text="📊 Статистика колод", callback_data="admin:stats")
    builder.button(text="📚 Список колод (HSGuru)", callback_data="admin:decklist")
    builder.button(text="📝 Архетипы (названия колод)", callback_data="admin:archetypes")
    if config.HSGURU_ENABLED:
        builder.button(text="⏸ Остановить постинг колод", callback_data="admin:posting_stop")
        builder.button(text="▶ Возобновить постинг колод", callback_data="admin:posting_resume")
    builder.adjust(1)
    return builder.as_markup()


# Текст кнопок нижней клавиатуры (админ-меню)
BTN_ADMIN_TEST = "🧪 Проверить отправку колод"
BTN_ADMIN_STATS = "📊 Статистика колод"
BTN_DECK_LIST = "📚 Список колод (HSGuru)"
BTN_ARCH_LIST = "📋 Архетипы: список"
BTN_ARCH_ADD = "➕ Архетипы: добавить"
BTN_ARCH_EDIT = "✏️ Архетипы: изменить"
BTN_STOP_POSTING = "⏸ Остановить постинг колод"
BTN_RESUME_POSTING = "▶ Возобновить постинг колод"


def build_admin_reply_keyboard() -> Optional[ReplyKeyboardMarkup]:
    """Нижняя (reply) клавиатура для админа: единое меню с основными действиями."""
    if not config.ADMIN_IDS:
        return None
    rows = [
        [KeyboardButton(text=BTN_ADMIN_TEST)],
        [KeyboardButton(text=BTN_ADMIN_STATS)],
        [KeyboardButton(text=BTN_DECK_LIST)],
        [KeyboardButton(text=BTN_ARCH_LIST)],
        [KeyboardButton(text=BTN_ARCH_ADD), KeyboardButton(text=BTN_ARCH_EDIT)],
    ]
    if config.HSGURU_ENABLED:
        from hsguru_scraper import get_posting_paused
        label = BTN_RESUME_POSTING if get_posting_paused() else BTN_STOP_POSTING
        rows.append([KeyboardButton(text=label)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in config.ADMIN_IDS


async def notify_admins(text: str, parse_mode: str = 'HTML'):
    """Отправляет уведомление всем администраторам."""
    if not bot or not config.ADMIN_IDS:
        return
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=parse_mode)
        except Exception as e:
            print(f"Не удалось отправить уведомление админу {admin_id}: {e}")


def register_handlers(dp_instance: Dispatcher):
    """Регистрирует все обработчики команд и сообщений."""
    
    # Регистрируем обработчик /start
    @dp_instance.message(Command("start"))
    async def cmd_start(message: Message):
        """Обработчик команды /start."""
        await message.answer(
            "Привет! Я бот для визуализации колод Hearthstone.\n\n"
            "Просто отправь мне код колоды (начинается с AAE), "
            "и я создам изображение с картами!"
        )
        # Админу сразу показываем нижнюю клавиатуру (первым сообщением с reply_markup)
        if is_admin(message.from_user.id):
            reply_kb = build_admin_reply_keyboard()
            if reply_kb:
                await message.answer(
                    "📮 Управление постингом колод — кнопка ниже.",
                    reply_markup=reply_kb,
                )
    
    @dp_instance.message(Command("help"))
    async def cmd_help(message: Message):
        """Обработчик команды /help."""
        help_text = (
            "📋 <b>Список команд бота:</b>\n\n"
            "🎴 <b>Визуализация колод:</b>\n"
            "• Отправь код колоды (начинается с AAE) - бот создаст изображение колоды\n\n"
            "🖼️ <b>/image &lt;название карты&gt;</b>\n"
            "• Показывает изображение карты\n"
            "• Пример: <code>/image Reno</code>\n\n"
            "🔎 <b>/search_deck &lt;название карты&gt;</b>\n"
            "• Ищет колоды, содержащие указанную карту\n"
            "• Пример: <code>/search_deck Reno</code>\n\n"
            "🌐 <b>/wp &lt;код колоды&gt;</b>\n"
            "• Загружает картинку колоды в WordPress (если настроено)\n\n"
            "ℹ️ <b>/help</b> - показать эту справку\n\n"
            "Бот работает в личных сообщениях, группах и каналах."
        )
        await message.answer(help_text, parse_mode='HTML')
    
    def _admin_panel_text() -> str:
        """Текст админ-панели с актуальным статусом постинга."""
        text = "🔧 <b>Админ-панель</b>\n\nВыберите действие:"
        if config.HSGURU_ENABLED:
            from hsguru_scraper import get_posting_paused
            status = "приостановлен ⏸" if get_posting_paused() else "активен ▶"
            text += f"\n\n📮 <b>Постинг колод:</b> {status}"
        return text

    async def _send_admin_test(target: Message) -> None:
        """Проверка генерации колоды (используется и в меню, и в инлайн-кнопке)."""
        # Тестовый код колоды (Priest deck)
        test_deck = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
        try:
            result = await process_deck_string(test_deck)
            if result:
                image_bytes, metadata = result
                test_text = (
                    "✅ <b>Тест отправки пройден!</b>\n\n"
                    f"📦 Размер изображения: {len(image_bytes.getvalue()):,} байт\n"
                    f"⚔️ Режим: {metadata.get('format_name', 'N/A')}\n"
                    f"💎 Пыль: {metadata.get('dust_cost', 0):,}\n\n"
                    "Генерация работает корректно! ✓"
                )
                image_file = BufferedInputFile(
                    image_bytes.getvalue(),
                    filename="test_deck.png"
                )
                await target.answer_photo(
                    photo=image_file,
                    caption=test_text,
                    parse_mode='HTML'
                )
            else:
                await target.answer(
                    "❌ <b>Тест не пройден!</b>\n\n"
                    "Не удалось сгенерировать изображение колоды.",
                    parse_mode='HTML'
                )
        except Exception as e:
            await target.answer(
                f"❌ <b>Ошибка теста:</b>\n\n<code>{str(e)}</code>",
                parse_mode='HTML'
            )

    async def _send_admin_stats(target: Message) -> None:
        """Отправка статистики колод (используется и в меню, и в инлайн-кнопке)."""
        try:
            stats = deck_db.get_statistics()
            modes_text = ""
            if stats["top_modes"]:
                modes_list = [f"  • {mode}: {cnt}" for mode, cnt in stats["top_modes"]]
                modes_text = "\n".join(modes_list)
            else:
                modes_text = "  Нет данных"
            stats_text = (
                "📊 <b>Статистика колод</b>\n\n"
                f"📦 <b>Всего колод:</b> {stats['total_decks']:,}\n"
                f"📅 <b>Сегодня:</b> {stats['today_decks']:,}\n"
                f"📆 <b>За 7 дней:</b> {stats['week_decks']:,}\n\n"
                f"👍 <b>Лайков:</b> {stats['total_likes']:,}\n"
                f"👎 <b>Дизлайков:</b> {stats['total_dislikes']:,}\n\n"
                f"🎮 <b>Топ режимов:</b>\n{modes_text}"
            )
            await target.answer(stats_text, parse_mode='HTML')
        except Exception as e:
            await target.answer(
                f"❌ <b>Ошибка получения статистики:</b>\n\n<code>{str(e)}</code>",
                parse_mode='HTML'
            )

    def _build_archetypes_page(page: int, page_size: int = 15) -> Tuple[str, InlineKeyboardMarkup]:
        from hsguru_scraper import get_archetypes_list
        items = get_archetypes_list()
        total = len(items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        chunk = items[start:start + page_size]
        if not chunk:
            text = "📋 <b>Архетипы</b>\n\nСписок пуст."
        else:
            lines = [f"• <b>{eng}</b> → {rus}" for eng, rus in chunk]
            text = f"📋 <b>Архетипы</b> (стр. {page}/{total_pages})\n\n" + "\n".join(lines)
        builder = InlineKeyboardBuilder()
        if page > 1:
            builder.button(text="⬅️ Назад", callback_data=f"admin:archetypes_page:{page - 1}")
        if page < total_pages:
            builder.button(text="Вперёд ➡️", callback_data=f"admin:archetypes_page:{page + 1}")
        builder.adjust(2)
        return text, builder.as_markup()

    async def _get_admin_deck_list(user_id: int, force_refresh: bool = False) -> List[Dict]:
        """Загружает список колод с HSGuru без фильтрации (кэшируется на 20 минут)."""
        cache = _admin_deck_cache.get(user_id)
        if cache and not force_refresh:
            ts = cache.get("ts")
            if isinstance(ts, datetime) and (datetime.now() - ts) < timedelta(minutes=20):
                return cache.get("decks", [])
        from hsguru_scraper import fetch_html, parse_decks, load_archetypes
        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(None, fetch_html)
        archetypes = load_archetypes()
        decks = parse_decks(html, archetypes)
        # Предрасчёт для ускорения листания
        for deck in decks:
            deck_code = deck.get("deck_code", "")
            deck_format_name = deck.get("format", "")
            deck["dust_cost"] = _compute_dust_cost_fast(deck_code)
            deck_class_name, deck_mode_name = _resolve_deck_class_mode(
                deck_code, deck.get("format"), deck_format_name
            )
            deck["deck_class_name"] = deck_class_name
            deck["deck_mode_name"] = deck_mode_name
        _admin_deck_cache[user_id] = {"decks": decks, "ts": datetime.now()}
        return decks

    async def _build_decklist_page(user_id: int, page: int, page_size: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
        decks = await _get_admin_deck_list(user_id)
        total = len(decks)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        index = (page - 1) * page_size
        deck = decks[index] if decks else None

        if not deck:
            text = "📚 <b>Список колод (HSGuru)</b>\n\nСписок пуст."
        else:
            wins = deck.get("wins", 0) or 0
            losses = deck.get("losses", 0) or 0
            deck_code = deck.get("deck_code", "")
            deck_name = deck.get("deck_name", "Без названия")
            streamer = deck.get("streamer", "Неизвестный")
            dust_cost = deck.get("dust_cost", 0)
            deck_class_name = deck.get("deck_class_name")
            deck_mode_name = deck.get("deck_mode_name") or deck.get("format", "")
            caption = _build_channel_caption(
                deck_name=deck_name,
                streamer=streamer,
                wins=int(wins),
                losses=int(losses),
                deck_code=deck_code,
                deck_class=deck_class_name,
                deck_mode=deck_mode_name,
                dust_cost=dust_cost,
            )
            text = f"📚 <b>Список колод (HSGuru)</b> (стр. {page}/{total_pages})\n\n{caption}"

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Обновить", callback_data="admin:decklist:refresh")
        if page > 1:
            builder.button(text="⬅️ Назад", callback_data=f"admin:decklist:page:{page - 1}")
        if page < total_pages:
            builder.button(text="Далее →", callback_data=f"admin:decklist:page:{page + 1}")
        builder.button(text="Опубликовать в WordPress", callback_data=f"admin:decklist:wp:{page}")
        builder.button(text="Опубликовать в TG", callback_data=f"admin:decklist:tg:{page}")
        builder.adjust(1, 2, 2)
        return text, builder.as_markup()

    def _compute_dust_cost_fast(deck_code: str) -> int:
        """Быстрый расчет пыли без генерации изображения."""
        if not deck_code or not generator:
            return 0
        try:
            try:
                deck = hearthstone.deckstrings.Deck.from_deckstring(deck_code)
            except AttributeError:
                deck = hearthstone.deckstrings.parse_deckstring(deck_code)
            cards = list(deck.cards)
            sideboards = []
            if hasattr(deck, "sideboards") and deck.sideboards:
                if isinstance(deck.sideboards, dict):
                    for items in deck.sideboards.values():
                        sideboards.extend(items)
                elif isinstance(deck.sideboards, list):
                    for item in deck.sideboards:
                        if isinstance(item, tuple) and len(item) == 2:
                            sideboards.extend(item[1])
            all_cards = list(cards) + list(sideboards)
            return int(generator.calculate_dust_cost(all_cards))
        except Exception:
            return 0

    def _get_deck_by_page(user_id: int, page: int, page_size: int = 1) -> Optional[Dict]:
        decks = _admin_deck_cache.get(user_id, {}).get("decks") or []
        if not decks:
            return None
        total_pages = max(1, (len(decks) + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        index = (page - 1) * page_size
        return decks[index] if index < len(decks) else None

    @dp_instance.message(Command("admin"))
    async def cmd_admin(message: Message):
        """Обработчик команды /admin - админ панель."""
        if not is_admin(message.from_user.id):
            await message.answer("⛔ У вас нет доступа к админ-панели.")
            return
        reply_kb = build_admin_reply_keyboard()
        await message.answer(
            _admin_panel_text() + "\n\nИспользуйте меню ниже.",
            parse_mode='HTML',
            reply_markup=reply_kb,
        )
    
    @dp_instance.callback_query(F.data == "admin:test")
    async def handle_admin_test(callback: CallbackQuery):
        """Обработчик кнопки проверки отправки колод."""
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer("🔄 Проверяю...")
        await _send_admin_test(callback.message)
    
    @dp_instance.callback_query(F.data == "admin:stats")
    async def handle_admin_stats(callback: CallbackQuery):
        """Обработчик кнопки статистики колод."""
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer("📊 Загружаю статистику...")
        await _send_admin_stats(callback.message)

    @dp_instance.callback_query(F.data == "admin:posting_stop")
    async def handle_admin_posting_stop(callback: CallbackQuery):
        """Остановить автоматический постинг колод HSGuru."""
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        from hsguru_scraper import set_posting_paused
        set_posting_paused(True)
        await callback.answer("⏸ Постинг колод приостановлен.", show_alert=True)
        await callback.message.edit_text(
            _admin_panel_text(),
            parse_mode='HTML',
            reply_markup=build_admin_keyboard()
        )

    @dp_instance.callback_query(F.data == "admin:posting_resume")
    async def handle_admin_posting_resume(callback: CallbackQuery):
        """Возобновить автоматический постинг колод HSGuru."""
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        from hsguru_scraper import set_posting_paused
        set_posting_paused(False)
        await callback.answer("▶ Постинг колод возобновлён.", show_alert=True)
        await callback.message.edit_text(
            _admin_panel_text(),
            parse_mode='HTML',
            reply_markup=build_admin_keyboard()
        )

    @dp_instance.message(F.text.in_({BTN_STOP_POSTING, BTN_RESUME_POSTING}))
    async def cmd_admin_toggle_posting(message: Message):
        """Переключение постинга колод по нажатию кнопки нижней клавиатуры (только админ)."""
        if not is_admin(message.from_user.id):
            return
        from hsguru_scraper import set_posting_paused
        if message.text == BTN_STOP_POSTING:
            set_posting_paused(True)
            await message.answer("⏸ Постинг колод приостановлен.", reply_markup=build_admin_reply_keyboard())
        else:
            set_posting_paused(False)
            await message.answer("▶ Постинг колод возобновлён.", reply_markup=build_admin_reply_keyboard())

    # ---------- Архетипы (англ. названия колод -> рус. перевод) ----------
    class ArchetypeStateFilter(Filter):
        async def __call__(self, message: Message, *args: object, **kwargs: object) -> bool:
            return bool(
                message.from_user
                and message.from_user.id in _archetype_state
                and is_admin(message.from_user.id)
            )

    @dp_instance.message(ArchetypeStateFilter(), F.text)
    async def cmd_archetype_state_message(message: Message):
        """Обработка ввода при добавлении/редактировании архетипа (только админ в состоянии)."""
        uid = message.from_user.id
        state = _archetype_state.get(uid)
        if not state:
            return
        text = (message.text or "").strip()
        if not text:
            await message.answer("Введите непустой текст.")
            return
        from hsguru_scraper import add_archetype, update_archetype
        action = state.get("action")
        step = state.get("step")
        if action == "add":
            if step == 1:
                _archetype_state[uid] = {"action": "add", "step": 2, "eng": text}
                await message.answer("Введите <b>русское</b> название колоды:", parse_mode="HTML")
            else:
                eng = state.get("eng", "")
                if add_archetype(eng, text):
                    await message.answer(f"✅ Добавлено: <b>{eng}</b> → {text}", parse_mode="HTML")
                else:
                    await message.answer("❌ Не удалось добавить. Проверьте файл Архетипы.csv.")
                del _archetype_state[uid]
        elif action == "edit":
            if step == 1:
                _archetype_state[uid] = {"action": "edit", "step": 2, "eng": text}
                await message.answer("Введите новый <b>русский</b> перевод:", parse_mode="HTML")
            else:
                eng = state.get("eng", "")
                if update_archetype(eng, text):
                    await message.answer(f"✅ Обновлено: <b>{eng}</b> → {text}", parse_mode="HTML")
                else:
                    await message.answer(f"❌ Архетип «{eng}» не найден или ошибка записи.")
                del _archetype_state[uid]

    @dp_instance.message(F.text.in_({BTN_ADMIN_TEST, BTN_ADMIN_STATS, BTN_DECK_LIST, BTN_ARCH_LIST, BTN_ARCH_ADD, BTN_ARCH_EDIT}))
    async def cmd_admin_menu_actions(message: Message):
        """Обработчик пунктов нижнего админ-меню (reply клавиатура)."""
        if not is_admin(message.from_user.id):
            return
        if message.text == BTN_ADMIN_TEST:
            await _send_admin_test(message)
            return
        if message.text == BTN_ADMIN_STATS:
            await _send_admin_stats(message)
            return
        if message.text == BTN_DECK_LIST:
            text, kb = await _build_decklist_page(message.from_user.id, 1)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if message.text == BTN_ARCH_LIST:
            text, kb = _build_archetypes_page(1)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if message.text == BTN_ARCH_ADD:
            _archetype_state[message.from_user.id] = {"action": "add", "step": 1}
            await message.answer("Введите <b>английское</b> название колоды:", parse_mode="HTML")
            return
        if message.text == BTN_ARCH_EDIT:
            _archetype_state[message.from_user.id] = {"action": "edit", "step": 1}
            await message.answer("Введите <b>английское</b> название колоды для изменения перевода:", parse_mode="HTML")
            return

    @dp_instance.callback_query(F.data == "admin:archetypes")
    async def handle_admin_archetypes(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        text, kb = _build_archetypes_page(1)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp_instance.callback_query(F.data == "admin:archetypes_list")
    async def handle_archetypes_list(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        text, kb = _build_archetypes_page(1)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp_instance.callback_query(F.data.startswith("admin:archetypes_page:"))
    async def handle_archetypes_page(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        try:
            page = int(callback.data.split(":")[-1])
        except Exception:
            page = 1
        text, kb = _build_archetypes_page(page)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp_instance.callback_query(F.data == "admin:archetypes_add")
    async def handle_archetypes_add(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        uid = callback.from_user.id
        _archetype_state[uid] = {"action": "add", "step": 1}
        await callback.answer()
        await callback.message.answer("Введите <b>английское</b> название колоды:", parse_mode="HTML")

    @dp_instance.callback_query(F.data == "admin:archetypes_edit")
    async def handle_archetypes_edit(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        uid = callback.from_user.id
        _archetype_state[uid] = {"action": "edit", "step": 1}
        await callback.answer()
        await callback.message.answer("Введите <b>английское</b> название колоды для изменения перевода:", parse_mode="HTML")

    # ---------- Список колод HSGuru (без фильтра) ----------
    @dp_instance.callback_query(F.data == "admin:decklist")
    async def handle_admin_decklist(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        text, kb = await _build_decklist_page(callback.from_user.id, 1)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp_instance.callback_query(F.data.startswith("admin:decklist:page:"))
    async def handle_admin_decklist_page(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        try:
            page = int(callback.data.split(":")[-1])
        except Exception:
            page = 1
        text, kb = await _build_decklist_page(callback.from_user.id, page)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp_instance.callback_query(F.data == "admin:decklist:refresh")
    async def handle_admin_decklist_refresh(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer("🔄 Обновляю список...")
        _admin_deck_cache.pop(callback.from_user.id, None)
        text, kb = await _build_decklist_page(callback.from_user.id, 1)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    def _check_manual_publish(deck: Dict) -> Tuple[bool, str, Set[int], int]:
        from hsguru_scraper import get_deck_cards_set, is_duplicate_deck, load_seen
        wins = deck.get("wins", 0) or 0
        losses = deck.get("losses", 0) or 0
        total_games = deck.get("total_games", 0)
        if total_games == 0:
            total_games = wins + losses
        if total_games < MIN_GAMES:
            return False, f"Колода имеет {total_games} игр < {MIN_GAMES}.", set(), total_games
        deck_code = deck.get("deck_code", "")
        deck_cards = get_deck_cards_set(deck_code)
        seen_data = load_seen()
        if is_duplicate_deck(deck_code, deck_cards, seen_data):
            return False, "Колода является дубликатом (код/карты).", deck_cards, total_games
        return True, "", deck_cards, total_games

    def _update_seen_after_publish(deck: Dict, deck_cards: Set[int]) -> None:
        from hsguru_scraper import load_seen, save_seen, FORMAT_MAP
        seen_data = load_seen()
        deck_code = deck.get("deck_code")
        if not deck_code:
            return
        seen_data["codes"].add(deck_code)
        if "decks" not in seen_data:
            seen_data["decks"] = {}
        deck_format = deck.get("format", "")
        deck_mode_normalized = FORMAT_MAP.get(deck_format, deck_format)
        seen_data["decks"][deck_code] = {
            "cards": deck_cards,
            "published_at": datetime.now().isoformat(),
            "format": deck_mode_normalized,
        }
        seen_data["last_published_format"] = deck_mode_normalized
        save_seen(seen_data)

    @dp_instance.callback_query(F.data.startswith("admin:decklist:wp:"))
    async def handle_admin_decklist_wp(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        try:
            page = int(callback.data.split(":")[-1])
        except Exception:
            page = 1
        deck = _get_deck_by_page(callback.from_user.id, page)
        if not deck:
            await callback.message.answer("Колода не найдена. Обновите список.")
            return
        ok, reason, deck_cards, total_games = _check_manual_publish(deck)
        if not ok:
            await callback.message.answer(f"❌ Публикация в WordPress отменена: {reason}")
            return
        payload = {
            "deck_code": deck.get("deck_code"),
            "deck_name": deck.get("deck_name"),
            "streamer": deck.get("streamer"),
            "player": deck.get("streamer"),
            "format": deck.get("format"),
            "wins": int(deck.get("wins", 0) or 0),
            "losses": int(deck.get("losses", 0) or 0),
            "win_loss": deck.get("win_loss", ""),
            "total_games": int(total_games),
            "peak": deck.get("peak", "") or "",
            "latest": deck.get("latest", "") or "",
            "worst": deck.get("worst", "") or "",
            "legend_rank": deck.get("legend_rank", "") or "",
        }
        result = await publish_hsguru_deck(payload, allow_telegram=False)
        if result:
            _update_seen_after_publish(deck, deck_cards)
            await callback.message.answer("✅ Опубликовано в WordPress.")
        else:
            await callback.message.answer("❌ Ошибка публикации в WordPress.")

    @dp_instance.callback_query(F.data.startswith("admin:decklist:tg:"))
    async def handle_admin_decklist_tg(callback: CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        await callback.answer()
        try:
            page = int(callback.data.split(":")[-1])
        except Exception:
            page = 1
        deck = _get_deck_by_page(callback.from_user.id, page)
        if not deck:
            await callback.message.answer("Колода не найдена. Обновите список.")
            return
        ok, reason, deck_cards, total_games = _check_manual_publish(deck)
        if not ok:
            await callback.message.answer(f"❌ Публикация в TG отменена: {reason}")
            return
        deck_code = deck.get("deck_code", "")
        result = await process_deck_string(deck_code)
        if not result:
            await callback.message.answer("❌ Не удалось обработать код колоды.")
            return
        image_bytes, metadata = result
        dust_cost = metadata.get("dust_cost", 0)
        deck_format_name = metadata.get("format_name")
        deck_class_name, deck_mode_name = _resolve_deck_class_mode(
            deck_code, deck.get("format"), deck_format_name
        )
        published = await publish_deck_to_telegram_channel(
            image_bytes=image_bytes,
            deck_name=deck.get("deck_name", "HSGuru Deck"),
            streamer=deck.get("streamer", "Неизвестный"),
            wins=int(deck.get("wins", 0) or 0),
            losses=int(deck.get("losses", 0) or 0),
            deck_code=deck_code,
            deck_class=deck_class_name,
            deck_mode=deck_mode_name,
            dust_cost=dust_cost,
        )
        if published:
            _update_seen_after_publish(deck, deck_cards)
            await callback.message.answer("✅ Опубликовано в TG.")
        else:
            await callback.message.answer("❌ Ошибка публикации в TG.")
    
    @dp_instance.message(Command("post"))
    async def cmd_post(message: Message):
        """Обработчик команды /post - парсинг HSGuru и публикация на WordPress с проверкой интервала 30 минут."""
        if not is_admin(message.from_user.id):
            await message.answer("⛔ У вас нет доступа к этой команде.")
            return
        
        # Парсим аргументы: /post или /post 1
        args = message.text.split()
        limit = 1  # По умолчанию 1 колода (как в автоматическом режиме)
        if len(args) > 1 and args[1].isdigit():
            limit = min(int(args[1]), 1)  # Максимум 1 колода за раз (как в автоматическом режиме)
        
        processing_msg = await message.reply(
            f"🔄 Парсинг HSGuru с полной статистикой...\n"
            f"📊 Лимит: {limit} колода\n"
            f"⏱ Интервал: 30 минут"
        )
        
        try:
            # Используем новый парсер из hsguru_scraper с полной статистикой
            from hsguru_scraper import (
                fetch_html,
                parse_decks,
                load_archetypes,
                load_seen,
                save_seen,
                get_deck_cards_set,
                is_duplicate_deck,
            )
            
            # Загружаем архетипы для перевода
            archetypes = load_archetypes()
            
            # Загружаем HTML
            loop = asyncio.get_event_loop()
            html = await loop.run_in_executor(None, fetch_html)
            
            # Парсим колоды с полной статистикой
            decks = parse_decks(html, archetypes)
            
            if not decks:
                await processing_msg.edit_text("❌ Не удалось найти колоды на HSGuru.")
                return
            
            # Загружаем уже опубликованные
            seen_data = load_seen()
            seen_codes = seen_data.get("codes", set())
            
            # Фильтруем новые колоды и проверяем минимальное количество игр
            new_decks = []
            filtered_low_games = 0
            filtered_duplicates = 0
            
            for d in decks:
                deck_code = d.get("deck_code", "")
                if not deck_code:
                    continue

                deck_cards = get_deck_cards_set(deck_code)
                if not deck_cards:
                    print(f"[DEBUG /post] ⚠ Пропуск: не удалось извлечь карты из {deck_code[:20]}...")
                    continue

                if is_duplicate_deck(deck_code, deck_cards, seen_data):
                    filtered_duplicates += 1
                    print(f"[DEBUG /post] ❌ Дубликат (по коду или картам): {d['deck_name'][:30]}...")
                    continue

                wins_check = d.get("wins", 0) or 0
                losses_check = d.get("losses", 0) or 0
                total_games_check = d.get("total_games", 0)
                # Если total_games не установлен, вычисляем из wins + losses
                if total_games_check == 0:
                    total_games_check = wins_check + losses_check

                # Пропускаем колоды с <20 игр (включая 0)
                print(f"[DEBUG /post] Проверка колоды '{d['deck_name'][:30]}...': {wins_check}-{losses_check} (total={total_games_check})")
                if total_games_check < MIN_GAMES:
                    filtered_low_games += 1
                    print(f"[DEBUG /post] ❌ Фильтруем колоду '{d['deck_name'][:30]}...': {wins_check}-{losses_check} ({total_games_check} игр) < {MIN_GAMES}")
                    continue
                print(f"[DEBUG /post] ✅ Колода прошла фильтрацию: {total_games_check} игр >= {MIN_GAMES}")
                d["cards"] = deck_cards
                new_decks.append(d)

            print(
                f"[DEBUG /post] Фильтрация завершена: "
                f"новых={len(new_decks)}, "
                f"дубликаты={filtered_duplicates}, "
                f"мало_игр={filtered_low_games}"
            )
            
            if not new_decks:
                msg = (
                    f"ℹ️ Нет новых колод для публикации.\n"
                    f"📊 Всего на HSGuru: {len(decks)}\n"
                    f"✅ Уже опубликовано: {len(seen_codes)}"
                )
                if filtered_duplicates > 0:
                    msg += f"\n⚠️ Дубликаты (код/карты): {filtered_duplicates}"
                if filtered_low_games > 0:
                    msg += f"\n⚠️ Отфильтровано с <{MIN_GAMES} игр: {filtered_low_games}"
                await processing_msg.edit_text(msg)
                return
            
            # Берем первую колоду (как в автоматическом режиме)
            deck = new_decks[0]
            
            # Проверяем статистику перед публикацией (включая 0)
            wins = deck.get("wins", 0) or 0
            losses = deck.get("losses", 0) or 0
            total_games = deck.get("total_games", 0)
            # Если total_games не установлен, вычисляем из wins + losses
            if total_games == 0:
                total_games = wins + losses
            
            print(f"[DEBUG /post] ФИНАЛЬНАЯ ПРОВЕРКА: Колода '{deck['deck_name']}' - {wins}-{losses} (total={total_games})")
            if total_games < MIN_GAMES:
                print(f"[DEBUG /post] ФИНАЛЬНАЯ ПРОВЕРКА: Колода '{deck['deck_name']}' имеет {wins}-{losses} ({total_games} игр) < {MIN_GAMES}")
                await processing_msg.edit_text(
                    f"⚠️ Колода '{deck['deck_name']}' имеет только {total_games} игр ({wins}-{losses}) < {MIN_GAMES}.\n"
                    f"Пропускаем публикацию."
                )
                return
            
            # Получаем статистику ПЕРЕД проверкой и отображением
            wins = deck.get("wins", 0) or 0
            losses = deck.get("losses", 0) or 0
            # ВАЖНО: total_games должен быть вычислен правильно
            total_games_from_deck = deck.get("total_games", 0)
            if total_games_from_deck == 0:
                # Если total_games не был установлен, вычисляем из wins + losses
                total_games = wins + losses
            else:
                total_games = total_games_from_deck
            
            # Показываем информацию о колоде со статистикой
            stats_info = ""
            if wins > 0 or losses > 0:
                stats_info = f"\n📊 <b>Статистика:</b> {wins} побед, {losses} поражений"
            
            # ВСЕГДА показываем количество игр отдельной строкой
            games_info = f"\n🎮 <b>Всего игр:</b> {total_games}"
            if total_games < MIN_GAMES:
                games_info += f" ⚠️ <b>(меньше минимума {MIN_GAMES}!)</b>"
            else:
                games_info += f" ✅"
            
            ranks_info = ""
            if deck.get('peak') or deck.get('latest') or deck.get('legend_rank'):
                legend_label = deck.get('legend_rank') or 'N/A'
                ranks_info = (
                    f"\n🏆 <b>Ранги:</b> Peak={deck.get('peak', 'N/A')}, "
                    f"Latest={deck.get('latest', 'N/A')}, Legend={legend_label}"
                )
            
            await processing_msg.edit_text(
                f"📥 <b>Найдена новая колода:</b>\n\n"
                f"📛 <b>Название:</b> {deck['deck_name']}\n"
                f"🎮 <b>Стример:</b> {deck['streamer']}\n"
                f"⚔️ <b>Формат:</b> {deck['format']}"
                f"{stats_info}"
                f"{games_info}"
                f"{ranks_info}\n\n"
                f"🚀 Публикую в WordPress...",
                parse_mode='HTML'
            )
            
            # Формируем payload с полной статистикой (используем уже вычисленные значения)
            # КРИТИЧЕСКАЯ ПРОВЕРКА перед публикацией (последний барьер)
            print(f"[DEBUG /post] КРИТИЧЕСКАЯ ПРОВЕРКА перед публикацией:")
            print(f"   Колода: {deck['deck_name']}")
            print(f"   Статистика: {wins}-{losses} (total={total_games})")
            print(f"   Проверка: {total_games} < {MIN_GAMES} = {total_games < MIN_GAMES}")
            
            if total_games < MIN_GAMES:
                print(f"[DEBUG /post] КРИТИЧЕСКАЯ ПРОВЕРКА: Колода '{deck['deck_name']}' имеет {wins}-{losses} ({total_games} игр) < {MIN_GAMES}")
                await processing_msg.edit_text(
                    f"❌ <b>ОШИБКА ФИЛЬТРАЦИИ!</b>\n\n"
                    f"Колода '{deck['deck_name']}' имеет только {total_games} игр ({wins}-{losses}) < {MIN_GAMES}.\n"
                    f"Публикация отменена."
                )
                return
            
            payload = {
                "deck_code": deck["deck_code"],
                "deck_name": deck["deck_name"],
                "streamer": deck["streamer"],
                "player": deck["streamer"],
                "format": deck["format"],
                # Статистика (используем уже вычисленные значения)
                "wins": int(wins),
                "losses": int(losses),
                "win_loss": deck.get("win_loss", ""),
                "total_games": int(total_games),
                # Ранги
                "peak": deck.get("peak", "") or "",
                "latest": deck.get("latest", "") or "",
                "worst": deck.get("worst", "") or "",
                "legend_rank": deck.get("legend_rank", "") or "",
            }
            
            # Логируем для отладки
            print(f"[DEBUG /post] Payload stats: wins={payload['wins']}, losses={payload['losses']}, total={payload['total_games']}")
            
            try:
                result = await publish_hsguru_deck(payload)
            except NameError as e:
                if "MIN_GAMES" in str(e):
                    await processing_msg.edit_text(
                        f"❌ <b>КРИТИЧЕСКАЯ ОШИБКА!</b>\n\n"
                        f"Ошибка фильтрации: {e}\n"
                        f"Публикация отменена для безопасности."
                    )
                    print(f"[ERROR /post] NameError: {e}")
                    return
                raise
            
            if result:
                # Сохраняем в seen
                seen_data["codes"].add(deck["deck_code"])
                from datetime import datetime
                from hsguru_scraper import FORMAT_MAP
                if "decks" not in seen_data:
                    seen_data["decks"] = {}
                
                # Определяем нормализованный формат колоды
                deck_format = deck.get("format", "")
                deck_mode_normalized = FORMAT_MAP.get(deck_format, deck_format)
                
                seen_data["decks"][deck["deck_code"]] = {
                    "cards": deck.get("cards", set()),
                    "published_at": datetime.now().isoformat(),
                    "format": deck_mode_normalized  # Сохраняем режим колоды
                }
                # Обновляем режим последней опубликованной колоды
                if "last_published_format" not in seen_data:
                    seen_data["last_published_format"] = ""
                seen_data["last_published_format"] = deck_mode_normalized
                print(f"[Bot] 🔍 ДИАГНОСТИКА cmd_post: Обновлен last_published_format = '{deck_mode_normalized}'")
                save_seen(seen_data)
                
                # Итоговое сообщение со статистикой
                final_stats = ""
                if deck.get('total_games', 0) > 0:
                    final_stats = f"\n📊 Статистика: {deck['wins']}-{deck['losses']} ({deck['total_games']} игр)"
                
                await processing_msg.edit_text(
                    f"✅ <b>Колода опубликована!</b>\n\n"
                    f"📛 <b>Название:</b> {deck['deck_name']}\n"
                    f"🎮 <b>Стример:</b> {deck['streamer']}\n"
                    f"⚔️ <b>Формат:</b> {deck['format']}"
                    f"{final_stats}\n\n"
                    f"📊 Всего на HSGuru: {len(decks)}\n"
                    f"📥 Новых колод: {len(new_decks)}\n"
                    f"📁 Всего в базе: {len(seen_data['codes'])}\n\n"
                    f"⏱ <b>Следующая публикация через 30 минут</b>",
                    parse_mode='HTML'
                )
            else:
                await processing_msg.edit_text(
                    f"❌ Ошибка публикации колоды:\n{deck['deck_name']}"
                )
            
        except NameError as e:
            # Критическая ошибка - неопределенная переменная (например MIN_GAMES)
            error_msg = str(e)
            import traceback
            traceback.print_exc()
            await processing_msg.edit_text(
                f"❌ <b>КРИТИЧЕСКАЯ ОШИБКА!</b>\n\n"
                f"Ошибка: {error_msg}\n"
                f"Публикация отменена для безопасности."
            )
            print(f"[ERROR /post] NameError: {e}")
            return
        except Exception as e:
            error_msg = str(e)
            import traceback
            traceback.print_exc()
            if "Cloudflare" in error_msg or "403" in error_msg:
                await processing_msg.edit_text(
                    f"❌ HSGuru заблокировал запрос (Cloudflare):\n<code>{error_msg}</code>",
                    parse_mode='HTML'
                )
            else:
                await processing_msg.edit_text(
                    f"❌ Ошибка:\n<code>{error_msg}</code>",
                    parse_mode='HTML'
                )
    
    @dp_instance.message(Command("image"))
    async def cmd_image(message: Message):
        """Обработчик команды /image <card_name>."""
        # Получаем название карты из команды
        command_parts = message.text.split(maxsplit=1)
        if len(command_parts) < 2:
            await message.answer(
                "❌ Укажите название карты.\n"
                "Пример: <code>/image Reno</code>",
                parse_mode='HTML'
            )
            return
        
        card_name = command_parts[1].strip()
        
        # Ищем карту
        card = card_db.search_card_by_name(card_name)
        
        if not card:
            await message.answer(f"❌ Карта '{card_name}' не найдена.")
            return
        
        # Получаем путь к изображению
        image_filename = f"{card['id']}.png"
        image_path = config.IMAGES_PATH / image_filename
        
        if not image_path.exists():
            await message.answer(
                f"❌ Изображение карты '{card['name']}' не найдено в папке."
            )
            return
        
        # Отправляем изображение
        try:
            # В aiogram 3.x для файлов с диска используем FSInputFile
            photo_file = FSInputFile(path=str(image_path))
            await message.reply_photo(
                photo=photo_file,
                caption=f"🃏 {card['name']}"
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке изображения: {e}")
    
    @dp_instance.message(Command("search_deck"))
    async def cmd_search_deck(message: Message):
        """Обработчик команды /search_deck <card_name>."""
        # Получаем название карты из команды
        command_parts = message.text.split(maxsplit=1)
        if len(command_parts) < 2:
            await message.answer(
                "❌ Укажите название карты.\n"
                "Пример: <code>/search_deck Reno</code>",
                parse_mode='HTML'
            )
            return
        
        card_name = command_parts[1].strip()
        
        # Ищем карту
        card = card_db.search_card_by_name(card_name)
        
        if not card:
            await message.answer(f"❌ Карта '{card_name}' не найдена.")
            return
        
        # Ищем колоды с этой картой
        deck_codes = deck_db.find_decks_containing_card(card['dbfId'], limit=5)
        
        if not deck_codes:
            await message.answer(
                f"🔎 Колоды с картой <b>{card['name']}</b> не найдены.",
                parse_mode='HTML'
            )
            return
        
        # Формируем ответ
        response = f"🔎 <b>Найдены колоды с {card['name']}:</b>\n\n"
        for i, deck_code in enumerate(deck_codes, 1):
            response += f"{i}. <code>{deck_code}</code>\n"
        
        await message.answer(response, parse_mode='HTML')

    @dp_instance.message(Command("force_publish"))
    async def cmd_force_publish(message: Message):
        """Принудительно публикует колоду и на сайт, и в Telegram канал."""
        if not is_admin(message.from_user.id):
            await message.reply("❌ Эта команда доступна только администраторам.")
            return
        
        processing_msg = await message.reply("⏳ Ищу новую колоду для принудительной публикации...")
        
        try:
            # Используем логику из hsguru_scraper для поиска колоды
            from hsguru_scraper import fetch_html, parse_decks, load_archetypes, load_seen, save_seen, get_deck_cards_set, is_duplicate_deck
            
            # Загружаем архетипы
            archetypes = load_archetypes()
            
            # Загружаем HTML (синхронная функция в executor)
            loop = asyncio.get_event_loop()
            html = await loop.run_in_executor(None, fetch_html)
            
            # Парсим колоды (синхронная функция в executor)
            decks = await loop.run_in_executor(None, parse_decks, html, archetypes)
            if not decks:
                await processing_msg.edit_text("❌ Не удалось получить колоды с HSGuru.")
                return
            
            # Загружаем уже опубликованные
            seen_data = load_seen()
            seen_codes = set(seen_data.get("codes", []))
            
            # Фильтруем новые колоды с проверкой дубликатов и минимального количества игр
            MIN_GAMES = 20
            new_decks = []
            filtered_low_games = 0
            
            for deck in decks:
                deck_code = deck["deck_code"]
                
                if deck_code in seen_codes:
                    continue
                
                # Извлекаем карты колоды
                deck_cards = get_deck_cards_set(deck_code)
                if not deck_cards:
                    continue
                
                # Проверяем на дубликаты
                if is_duplicate_deck(deck_code, deck_cards, seen_data):
                    continue
                
                # Проверяем минимальное количество игр
                wins_check = deck.get("wins", 0) or 0
                losses_check = deck.get("losses", 0) or 0
                total_games = deck.get("total_games", 0)
                if total_games == 0:
                    total_games = wins_check + losses_check
                
                if total_games < MIN_GAMES:
                    filtered_low_games += 1
                    continue
                
                new_decks.append({
                    **deck,
                    "cards": deck_cards
                })
            
            if not new_decks:
                msg = (
                    f"ℹ️ Нет новых колод для публикации.\n"
                    f"📊 Всего на HSGuru: {len(decks)}\n"
                    f"✅ Уже опубликовано: {len(seen_codes)}"
                )
                if filtered_low_games > 0:
                    msg += f"\n⚠️ Отфильтровано с <{MIN_GAMES} игр: {filtered_low_games}"
                await processing_msg.edit_text(msg)
                return
            
            # Берем первую колоду
            deck = new_decks[0]
            deck_cards = deck.get("cards", set())
            
            await processing_msg.edit_text(
                f"📥 <b>Найдена колода для публикации:</b>\n\n"
                f"📛 <b>Название:</b> {deck['deck_name']}\n"
                f"🎮 <b>Стример:</b> {deck.get('streamer', 'N/A')}\n"
                f"📊 <b>Игры:</b> {deck.get('wins', 0)}-{deck.get('losses', 0)}\n\n"
                f"🚀 Публикую на сайт и в Telegram канал...",
                parse_mode='HTML'
            )
            
            # Формируем payload
            payload = {
                "deck_code": deck["deck_code"],
                "deck_name": deck["deck_name"],
                "streamer": deck.get("streamer", ""),
                "player": deck.get("player", ""),
                "format": deck.get("format", ""),
                "dust": deck.get("dust", 0),
                "wins": deck.get("wins", 0),
                "losses": deck.get("losses", 0),
                "peak": deck.get("peak", ""),
                "latest": deck.get("latest", ""),
                "worst": deck.get("worst", ""),
                "legend_rank": deck.get("legend_rank", ""),
                "source_url": deck.get("source_url", ""),
            }
            
            # Публикуем с принудительным флагом для Telegram канала
            success = await publish_hsguru_deck(payload, force_telegram=True)
            
            if success:
                # Сохраняем как опубликованную
                seen_data["codes"].add(deck["deck_code"])
                from datetime import datetime
                from hsguru_scraper import FORMAT_MAP
                if "decks" not in seen_data:
                    seen_data["decks"] = {}
                
                # Определяем нормализованный формат колоды
                deck_format = deck.get("format", "")
                deck_mode_normalized = FORMAT_MAP.get(deck_format, deck_format)
                
                seen_data["decks"][deck["deck_code"]] = {
                    "cards": list(deck_cards),  # Преобразуем set в list для JSON
                    "published_at": datetime.now().isoformat(),
                    "format": deck_mode_normalized  # Сохраняем режим колоды
                }
                # Обновляем режим последней опубликованной колоды
                if "last_published_format" not in seen_data:
                    seen_data["last_published_format"] = ""
                seen_data["last_published_format"] = deck_mode_normalized
                print(f"[Bot] 🔍 ДИАГНОСТИКА cmd_force_publish: Обновлен last_published_format = '{deck_mode_normalized}'")
                save_seen(seen_data)
                
                await processing_msg.edit_text(
                    f"✅ <b>Колода успешно опубликована!</b>\n\n"
                    f"📛 <b>Название:</b> {deck['deck_name']}\n"
                    f"🎮 <b>Стример:</b> {deck.get('streamer', 'N/A')}\n"
                    f"📊 <b>Игры:</b> {deck.get('wins', 0)}-{deck.get('losses', 0)}\n\n"
                    f"🌐 ✅ Опубликовано на сайте\n"
                    f"📱 ✅ Опубликовано в Telegram канал",
                    parse_mode="HTML"
                )
            else:
                await processing_msg.edit_text("❌ Ошибка при публикации колоды.")
                
        except Exception as e:
            await processing_msg.edit_text(f"❌ Ошибка: {str(e)}")
            import traceback
            traceback.print_exc()

    @dp_instance.message(Command("wp"))
    async def cmd_wp(message: Message):
        """Обработчик команды /wp <deck_code>."""
        text = message.text or ""
        deck_string = extract_deck_string(text)
        if not deck_string:
            await message.answer(
                "❌ Укажите код колоды.\nПример: <code>/wp AAE...</code>",
                parse_mode='HTML'
            )
            return

        processing_msg = await message.reply("🔄 Загружаю в WordPress...")
        result = await process_deck_string(deck_string)
        if not result:
            await processing_msg.edit_text("❌ Не удалось обработать колоду.")
            return

        image_bytes, metadata = result
        filename = f"deck-{_deck_hash(deck_string)[:12]}.png"
        url = upload_deck_image(image_bytes, filename)
        if not url:
            await processing_msg.edit_text(
                "❌ WordPress не настроен или произошла ошибка загрузки."
            )
            return

        await processing_msg.edit_text(
            f"✅ Загружено в WordPress: {url}"
        )
    
    async def handle_deck_message(message: Message):
        """
        Обработчик текстовых сообщений.
        Проверяет наличие кода колоды и генерирует изображение.
        """
        text = message.text or message.caption or ""
        
        # Пропускаем команды (они обрабатываются отдельными обработчиками)
        if text.startswith('/'):
            return
        
        # Ищем код колоды в сообщении
        deck_string = extract_deck_string(text)
        
        if not deck_string:
            # Если код не найден, игнорируем сообщение
            return
        
        # Отправляем сообщение о том, что обрабатываем колоду
        send_text = message.answer if message.chat.type == "channel" else message.reply
        processing_msg = await send_text("🔄 Обрабатываю колоду...")
        
        try:
            # Декодируем колоду для получения данных о картах
            try:
                deck = hearthstone.deckstrings.Deck.from_deckstring(deck_string)
            except AttributeError:
                # Альтернативный способ для старых версий библиотеки
                deck = hearthstone.deckstrings.parse_deckstring(deck_string)
            
            # Получаем список карт основной колоды
            deck_cards = []
            for dbf_id, count in deck.cards:
                deck_cards.append((dbf_id, count))
            
            # Получаем сайдборды
            sideboards = {}
            if hasattr(deck, 'sideboards') and deck.sideboards:
                if isinstance(deck.sideboards, dict):
                    sideboards = {k: list(v) for k, v in deck.sideboards.items()}
                elif isinstance(deck.sideboards, list):
                    for item in deck.sideboards:
                        if isinstance(item, tuple) and len(item) == 2:
                            owner_dbfid, sideboard_cards = item
                            sideboards[owner_dbfid] = list(sideboard_cards)
            
            # Генерируем изображение
            result = await process_deck_string(deck_string)
            
            if result:
                image_bytes, metadata = result
                
                # Формируем подпись с метаданными
                format_name = metadata.get('format_name', 'Стандартный')
                dust_cost = metadata.get('dust_cost', 0)
                
                # Форматируем стоимость пыли с разделителями тысяч (пробелы)
                dust_formatted = f"{dust_cost:,}".replace(',', ' ')
                
                # Создаем подпись с кодом колоды в monospace
                caption = (
                    f"⚔️ Режим: {format_name}\n"
                    f"💎 Пыль: {dust_formatted}\n\n"
                    f"<code>{deck_string}</code>\n\n"
                    f"Больше авторского и полезного контента по Hearthstone ты можешь найти прямо "
                    f"<a href=\"https://t.me/tribute/app?startapp=sxz9\">здесь</a>!"
                )
                
                # Отправляем изображение
                # В aiogram 3.x нужно использовать BufferedInputFile для BytesIO
                # Используем getvalue() для получения всех данных из BytesIO
                image_file = BufferedInputFile(
                    image_bytes.getvalue(), 
                    filename="deck.png"
                )
                is_channel = message.chat.type == "channel"
                send_photo = message.reply_photo if not is_channel else message.answer_photo
                try:
                    sent_message = await send_photo(
                        photo=image_file,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=build_vote_keyboard(
                            message.message_id,
                            include_counts=not is_channel
                        ) if not is_channel else None
                    )
                except TelegramBadRequest:
                    # Фолбэк: если reply недоступен (например, в канале)
                    sent_message = await message.answer_photo(
                        photo=image_file,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=build_vote_keyboard(
                            message.message_id,
                            include_counts=not is_channel
                        ) if not is_channel else None
                    )
                if not is_channel:
                    await sent_message.edit_reply_markup(
                        reply_markup=build_vote_keyboard(sent_message.message_id)
                    )
                
                # Сохраняем колоду в базу данных
                try:
                    # Собираем все dbfId карт (основная колода + сайдборды)
                    all_card_dbf_ids = []
                    for dbf_id, count in deck_cards:
                        all_card_dbf_ids.append(dbf_id)
                    
                    # Добавляем карты из сайдбордов
                    for sideboard_cards in sideboards.values():
                        for dbf_id, count in sideboard_cards:
                            all_card_dbf_ids.append(dbf_id)
                    
                    # Сохраняем в БД
                    deck_db.save_deck(
                        deck_code=deck_string,
                        mode=format_name,
                        dust_cost=dust_cost,
                        card_dbf_ids=all_card_dbf_ids
                    )
                    
                    # Уведомления админам о создании колод отключены по запросу пользователя
                    # user_info = message.from_user
                    # user_name = user_info.full_name if user_info else "Неизвестный"
                    # user_username = f"@{user_info.username}" if user_info and user_info.username else ""
                    # chat_info = f"{message.chat.title}" if message.chat.title else "Личные сообщения"
                    # 
                    # notify_text = (
                    #     "🔔 <b>Новая колода!</b>\n\n"
                    #     f"👤 <b>Пользователь:</b> {user_name} {user_username}\n"
                    #     f"💬 <b>Чат:</b> {chat_info}\n"
                    #     f"⚔️ <b>Режим:</b> {format_name}\n"
                    #     f"💎 <b>Пыль:</b> {dust_cost:,}\n\n"
                    #     f"<code>{deck_string[:50]}...</code>"
                    # )
                    # asyncio.create_task(notify_admins(notify_text))
                    
                except Exception as e:
                    print(f"Ошибка при сохранении колоды в БД: {e}")
                
                # Удаляем сообщение о обработке
                try:
                    await processing_msg.delete()
                except:
                    pass
            else:
                await processing_msg.edit_text(
                    "❌ Не удалось обработать колоду. "
                    "Проверь, что код колоды правильный."
                )
                
        except TelegramBadRequest as e:
            await processing_msg.edit_text(
                f"❌ Ошибка при отправке изображения: {e}"
            )
        except Exception as e:
            await processing_msg.edit_text(
                f"❌ Произошла ошибка: {e}"
            )

    @dp_instance.message(F.text)
    async def handle_text_message(message: Message):
        await handle_deck_message(message)

    @dp_instance.channel_post()
    async def handle_channel_post(message: Message):
        await handle_deck_message(message)

    @dp_instance.callback_query(F.data.startswith("vote:"))
    async def handle_vote(callback: CallbackQuery):
        """Обработчик лайков/дизлайков для постов с колодами."""
        if not callback.data:
            return
        try:
            _, vote_type, msg_id_raw = callback.data.split(":", 2)
            message_id = int(msg_id_raw)
        except ValueError:
            await callback.answer("Ошибка обработки голоса.")
            return

        if not callback.message:
            await callback.answer()
            return

        result = deck_db.register_vote(message_id, callback.from_user.id, vote_type)
        if result is None:
            await callback.answer("Ошибка обработки голоса.")
            return
        if result.get("already"):
            await callback.answer("Ваш голос уже учтен.")
            return

        if callback.message and callback.message.chat.type != "channel":
            await callback.message.edit_reply_markup(
                reply_markup=build_vote_keyboard(message_id)
            )
        await callback.answer("Спасибо за оценку!")


def extract_deck_string(text: str) -> Optional[str]:
    """
    Извлекает код колоды из текста сообщения.
    Коды колод Hearthstone начинаются с "AAE".
    
    Args:
        text: Текст сообщения
        
    Returns:
        Код колоды или None, если не найден
    """
    # Ищем строки, начинающиеся с AAE (коды колод Hearthstone)
    pattern = r'AAE[a-zA-Z0-9+/=]+'
    matches = re.findall(pattern, text)
    
    if matches:
        # Возвращаем первый найденный код
        return matches[0]
    return None


async def process_deck_string(deck_string: str) -> Optional[Tuple[BytesIO, Dict]]:
    """
    Обрабатывает код колоды и генерирует изображение.
    
    Args:
        deck_string: Код колоды Hearthstone
        
    Returns:
        Кортеж (BytesIO объект с изображением, словарь с метаданными) или None в случае ошибки
    """
    try:
        # Проверяем кэш
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_img_path, cache_meta_path = _cache_paths(deck_string)
        if cache_img_path.exists() and cache_meta_path.exists():
            try:
                image_bytes = BytesIO(cache_img_path.read_bytes())
                metadata = json.loads(cache_meta_path.read_text(encoding="utf-8"))
                return image_bytes, metadata
            except Exception as e:
                print(f"Ошибка чтения кэша: {e}")

        # Декодируем строку колоды
        try:
            deck = hearthstone.deckstrings.Deck.from_deckstring(deck_string)
        except AttributeError:
            # Альтернативный способ для старых версий библиотеки
            deck = hearthstone.deckstrings.parse_deckstring(deck_string)
        
        # Получаем список карт основной колоды (dbf_id, count)
        deck_cards = []
        for dbf_id, count in deck.cards:
            deck_cards.append((dbf_id, count))
        
        # Получаем сайдборды, если они есть
        sideboards = {}
        if hasattr(deck, 'sideboards') and deck.sideboards:
            # sideboards может быть списком кортежей или словарем
            # Формат: [(owner_dbf_id, [(dbf_id, count), ...]), ...]
            # или: {owner_dbf_id: [(dbf_id, count), ...], ...}
            if isinstance(deck.sideboards, dict):
                sideboards = {k: list(v) for k, v in deck.sideboards.items()}
            elif isinstance(deck.sideboards, list):
                for item in deck.sideboards:
                    if isinstance(item, tuple) and len(item) == 2:
                        owner_dbfid, sideboard_cards = item
                        sideboards[owner_dbfid] = list(sideboard_cards)
        
        # Получаем формат и героя колоды
        deck_format = getattr(deck, 'format', None)
        hero_dbf_id = None
        if hasattr(deck, 'heroes') and deck.heroes:
            hero_dbf_id = deck.heroes[0]
        
        # Генерируем изображение с учетом сайдбордов, формата и класса
        image_bytes, metadata = generator.generate_deck_image(
            deck_cards, sideboards, deck_format, hero_dbf_id
        )
        # Сохраняем в кэш
        try:
            cache_img_path.write_bytes(image_bytes.getvalue())
            cache_meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"Ошибка записи кэша: {e}")

        # Экспортируем изображение на сервер (локальная папка), если включено
        if config.IMAGE_EXPORT_ENABLED:
            try:
                export_dir = config.IMAGE_EXPORT_DIR
                export_dir.mkdir(parents=True, exist_ok=True)
                export_path = export_dir / f"{_deck_hash(deck_string)}.png"
                if not export_path.exists():
                    export_path.write_bytes(image_bytes.getvalue())
            except Exception as e:
                print(f"Ошибка экспорта изображения: {e}")
        return image_bytes, metadata
        
    except Exception as e:
        print(f"Ошибка при обработке колоды: {e}")
        import traceback
        traceback.print_exc()
        return None


def _build_channel_caption(deck_name: str, streamer: str, wins: int, losses: int,
                           deck_code: str, deck_class: Optional[str] = None,
                           deck_mode: Optional[str] = None, dust_cost: Optional[int] = None) -> str:
    """Формирует текст колоды в формате канала dcboom_hs."""
    total_games = wins + losses
    winrate = (wins / total_games * 100) if total_games > 0 else 0
    if winrate >= 60:
        winrate_emoji = "🔥"
    elif winrate >= 50:
        winrate_emoji = "✅"
    else:
        winrate_emoji = "📊"
    caption_parts = [
        f"<b>⚔️ {deck_name}</b>",
        ""
    ]
    if deck_class or deck_mode:
        mode_info = []
        if deck_class:
            mode_info.append(f"<b>{deck_class}</b>")
        if deck_mode:
            mode_info.append(deck_mode)
        if mode_info:
            caption_parts.append(f"🎯 {' • '.join(mode_info)}")
    caption_parts.append(f"👤 <b>Стример:</b> {streamer}")
    caption_parts.append(f"🎮 <b>Игр:</b> {total_games}")
    caption_parts.append(f"{winrate_emoji} <b>Винрейт:</b> {winrate:.1f}% <i>({wins}–{losses})</i>")
    if dust_cost and dust_cost > 0:
        caption_parts.append(f"💎 <b>Пыль:</b> {dust_cost:,}")
    caption_parts.append("")
    caption_parts.append(f"<code>{deck_code}</code>")
    return "\n".join(caption_parts)


async def publish_deck_to_telegram_channel(image_bytes: BytesIO, deck_name: str, streamer: str, 
                                          wins: int, losses: int, deck_code: str,
                                          deck_class: Optional[str] = None,
                                          deck_mode: Optional[str] = None,
                                          dust_cost: Optional[int] = None) -> bool:
    """
    Публикует колоду в Telegram канал с красивым оформлением.
    
    Args:
        image_bytes: BytesIO объект с изображением колоды
        deck_name: Название колоды
        streamer: Имя стримера
        wins: Количество побед
        losses: Количество поражений
        deck_code: Код колоды
        deck_class: Класс колоды (опционально)
        deck_mode: Режим колоды (опционально)
        dust_cost: Стоимость пыли (опционально)
        
    Returns:
        True если публикация успешна, False иначе
    """
    if not config.CHANNEL_ID:
        print("[Telegram Channel] ⚠ CHANNEL_ID не установлен, пропускаем публикацию в канал")
        return False
    
    try:
        image_bytes = _maybe_override_deck_art(deck_name, image_bytes)
        caption = _build_channel_caption(
            deck_name=deck_name,
            streamer=streamer,
            wins=wins,
            losses=losses,
            deck_code=deck_code,
            deck_class=deck_class,
            deck_mode=deck_mode,
            dust_cost=dust_cost,
        )
        
        # Подготавливаем изображение
        image_bytes.seek(0)
        photo = BufferedInputFile(image_bytes.read(), filename="deck.png")
        
        # Отправляем в канал с красивым оформлением
        await bot.send_photo(
            chat_id=config.CHANNEL_ID,
            photo=photo,
            caption=caption,
            parse_mode="HTML"
        )
        
        print(f"[Telegram Channel] ✅ Колода опубликована в канал: {deck_name}")
        return True
        
    except Exception as e:
        print(f"[Telegram Channel] ❌ Ошибка публикации в канал: {e}")
        import traceback
        traceback.print_exc()
        return False


def _resolve_deck_class_mode(deck_code: str, payload_format: Optional[str], deck_format_name: Optional[str]) -> Tuple[Optional[str], str]:
    """Определяет класс и режим колоды для публикации.
    Сначала пробует card_db по hero dbfId, при неудаче — HERO_CLASS_MAP по dbfId (чтобы класс всегда уходил в WordPress).
    """
    CLASS_MAP = {
        "DRUID": "Друид",
        "HUNTER": "Охотник",
        "MAGE": "Маг",
        "PALADIN": "Паладин",
        "PRIEST": "Жрец",
        "ROGUE": "Разбойник",
        "SHAMAN": "Шаман",
        "WARLOCK": "Чернокнижник",
        "WARRIOR": "Воин",
        "DEMONHUNTER": "Охотник на демонов",
        "DEATHKNIGHT": "Рыцарь смерти",
    }
    deck_class_name = None
    hero_dbf_id = None
    try:
        try:
            deck = hearthstone.deckstrings.Deck.from_deckstring(deck_code)
            hero_dbf_id = deck.heroes[0] if hasattr(deck, "heroes") and deck.heroes else None
        except AttributeError:
            parts = hearthstone.deckstrings.parse_deckstring(deck_code)
            hero_dbf_id = (parts[1][0] if len(parts) > 1 and parts[1] else None)
        if hero_dbf_id and card_db:
            hero_card = card_db.get_card(hero_dbf_id)
            class_key = (hero_card or {}).get("card_class", "") or (hero_card or {}).get("cardClass", "")
            class_key = (class_key or "").strip().upper()
            if class_key:
                deck_class_name = CLASS_MAP.get(class_key)
        if deck_class_name is None and hero_dbf_id is not None:
            deck_class_name = HERO_CLASS_MAP.get(hero_dbf_id)
            if deck_class_name:
                print(f"[Deck class] Резерв по dbfId {hero_dbf_id} -> {deck_class_name}")
        if deck_class_name is None and hero_dbf_id is not None:
            print(f"[Deck class] Не найден класс для hero dbfId={hero_dbf_id}. Добавьте его в HERO_CLASS_MAP.")
    except Exception as e:
        deck_class_name = None
        print(f"[Deck class] Ошибка определения класса: {e}")

    mode_map = {
        "Стандартный": "Стандарт",
        "Вольный": "Вольный",
        "Standard": "Стандарт",
        "Wild": "Вольный",
    }
    deck_mode_name = mode_map.get(payload_format, None) or mode_map.get(deck_format_name, "Стандарт")
    return deck_class_name, deck_mode_name


async def publish_hsguru_deck(payload: Dict, force_telegram: bool = False, allow_telegram: bool = True) -> bool:
    """
    Создает колоду из данных HSGuru.
    
    Args:
        payload: Словарь с данными колоды
        force_telegram: Если True, принудительно публикует в Telegram канал (игнорируя проверку времени)
    """
    deck_code = payload.get("deck_code")
    deck_name = payload.get("deck_name") or "HSGuru Deck"
    if not deck_code:
        return False
    result = await process_deck_string(deck_code)
    if not result:
        return False
    image_bytes, metadata = result
    image_bytes = _maybe_override_deck_art(deck_name, image_bytes)
    dust_cost = payload.get("dust")
    if dust_cost is None:
        dust_cost = metadata.get("dust_cost", 0)
    deck_format_name = metadata.get("format_name")

    payload_format = payload.get("format")
    deck_class_name, deck_mode_name = _resolve_deck_class_mode(deck_code, payload_format, deck_format_name)

    # Получаем статистику из payload
    wins = payload.get("wins")
    losses = payload.get("losses")
    peak = payload.get("peak", "")
    latest = payload.get("latest", "")
    worst = payload.get("worst", "")
    legend_rank = payload.get("legend_rank", "")
    
    # Преобразуем в числа, если они есть
    if wins is None:
        wins = 0
    else:
        try:
            wins = int(wins)
        except (ValueError, TypeError):
            wins = 0
    
    if losses is None:
        losses = 0
    else:
        try:
            losses = int(losses)
        except (ValueError, TypeError):
            losses = 0
    
    # Логируем статистику для отладки
    print(f"[DEBUG publish_hsguru_deck] Статистика из payload:")
    print(f"   wins={wins} (type: {type(wins).__name__})")
    print(f"   losses={losses} (type: {type(losses).__name__})")
    print(f"   total={wins + losses}")
    print(f"   Ранги: peak={peak}, latest={latest}, worst={worst}, legend={legend_rank}")
    
    success = create_hs_deck_post(
        deck_code=deck_code,
        deck_name=deck_name,
        streamer=payload.get("streamer"),
        player=payload.get("player"),
        dust_cost=dust_cost,
        source_url=payload.get("source_url"),
        image_bytes=image_bytes,
        deck_class=deck_class_name,
        deck_mode=deck_mode_name,
        # Статистика
        wins=int(wins),
        losses=int(losses),
        peak=str(peak),
        latest=str(latest),
        worst=str(worst),
        legend_rank=str(legend_rank),
    )
    
    # Публикуем в Telegram канал только если прошло 2 часа с последней публикации (или принудительно)
    global last_telegram_channel_publish
    streamer_info = payload.get("streamer") or "Неизвестный"
    
    # Проверяем нужно ли публиковать в канал (каждые 2 часа или принудительно)
    should_publish_to_channel = False
    if not allow_telegram:
        should_publish_to_channel = False
        print("[Telegram Channel] Публикация в канал отключена (allow_telegram=False)")
    elif force_telegram:
        # Принудительная публикация - игнорируем проверку времени
        should_publish_to_channel = True
        print("[Telegram Channel] 🔴 ПРИНУДИТЕЛЬНАЯ публикация в канал (игнорируем проверку времени)")
    elif last_telegram_channel_publish is None:
        # Первая публикация - публикуем сразу
        should_publish_to_channel = True
        print("[Telegram Channel] Первая публикация в канал")
    else:
        # Проверяем прошло ли 2 часа
        time_since_last = datetime.now() - last_telegram_channel_publish
        if time_since_last.total_seconds() >= 2 * 60 * 60:  # 2 часа
            should_publish_to_channel = True
            print(f"[Telegram Channel] Прошло {time_since_last.total_seconds() / 3600:.1f} часов, публикуем в канал")
        else:
            remaining_minutes = int((2 * 60 * 60 - time_since_last.total_seconds()) / 60)
            print(f"[Telegram Channel] Пропускаем публикацию в канал (осталось {remaining_minutes} мин до следующей)")
    
    channel_success = False
    if should_publish_to_channel:
        channel_success = await publish_deck_to_telegram_channel(
            image_bytes=image_bytes,
            deck_name=deck_name,
            streamer=streamer_info,
            wins=int(wins),
            losses=int(losses),
            deck_code=deck_code,
            deck_class=deck_class_name,
            deck_mode=deck_mode_name,
            dust_cost=dust_cost
        )
        if channel_success:
            last_telegram_channel_publish = datetime.now()
            print(f"[Telegram Channel] Время последней публикации обновлено: {last_telegram_channel_publish}")
    
    return success




async def main():
    """Основная функция запуска бота."""
    global bot, dp, card_db, generator, deck_db
    
    # Проверяем токен
    if not config.BOT_TOKEN:
        print("❌ ОШИБКА: BOT_TOKEN не установлен!")
        print("Создайте файл .env с содержимым:")
        print("BOT_TOKEN=your_bot_token_here")
        return
    
    print(f"✓ Токен загружен (длина: {len(config.BOT_TOKEN)} символов)")
    
    # Инициализируем бота и диспетчер
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    
    # Инициализируем базу данных карт
    print("Загрузка базы данных карт...")
    try:
        card_db = CardDatabase(config.JSON_PATH, config.JSON_RU_PATH)
        print(f"✓ Загружено карт: {len(card_db.cards)}")
    except Exception as e:
        print(f"❌ Ошибка загрузки базы карт: {e}")
        return
    
    # Инициализируем базу данных колод
    print("Инициализация базы данных колод...")
    try:
        deck_db = DeckDatabase()
        print("✓ База данных колод готова")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД колод: {e}")
        return
    
    # Инициализируем генератор изображений
    print(f"Инициализация генератора изображений (путь: {config.IMAGES_PATH})...")
    try:
        generator = DeckImageGenerator(card_db, config.IMAGES_PATH)
        print("✓ Генератор изображений готов")
    except Exception as e:
        print(f"❌ Ошибка инициализации генератора: {e}")
        return
    
    # Регистрируем обработчики
    print("Регистрация обработчиков...")
    try:
        register_handlers(dp)
        print("✓ Обработчики зарегистрированы")
    except Exception as e:
        print(f"❌ Ошибка регистрации обработчиков: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Проверяем, что бот может подключиться
    print("Проверка подключения к Telegram API...")
    try:
        bot_info = await bot.get_me()
        print(f"✓ Бот подключен: @{bot_info.username} ({bot_info.first_name})")
    except Exception as e:
        print(f"❌ Ошибка подключения к Telegram API: {e}")
        print("Проверьте правильность токена!")
        return

    # Запускаем парсер HSGuru внутри бота (если включено)
    if config.HSGURU_ENABLED:
        print("✓ Запуск парсера HSGuru...")
        # Передаем функцию для отправки статистики администраторам
        asyncio.create_task(hsguru_scraper.run_loop(publish_hsguru_deck, notify_admins))
    
    # Запускаем бота
    print("=" * 50)
    print("🤖 Бот запущен и готов к работе!")
    print("=" * 50)
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем.")
    except Exception as e:
        print(f"Критическая ошибка: {e}")

