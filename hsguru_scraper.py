"""
HSGuru Scraper - Автоматический парсер колод с hsguru.com
Работает 24/7, публикует по 1 колоде каждые 30 минут (максимум 2 в час).
"""
import asyncio
import csv
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Callable, Optional, Set, Tuple
from collections import deque
import cloudscraper
from bs4 import BeautifulSoup
import hearthstone.deckstrings as deckstrings

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback для Python < 3.9
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        # Если нет zoneinfo, используем pytz (нужно установить)
        try:
            import pytz
            ZoneInfo = pytz.timezone
        except ImportError:
            print("[HSGuru] ВНИМАНИЕ: Необходим pytz или zoneinfo для работы статистики!")
            ZoneInfo = None

import config


# ============================================================================
# CONSTANTS
# ============================================================================

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

# Paths
BASE_DIR = Path(__file__).parent
ARCHETYPES_CSV = BASE_DIR / "Архетипы.csv"

# Названия, которые являются просто именем класса и не должны использоваться
# для проверки дубликатов по имени (разные деки могут называться одинаково).
GENERIC_DECK_NAMES: Set[str] = {
    "paladin", "mage", "warrior", "demon hunter", "death knight", "shaman",
    "druid", "hunter", "priest", "rogue", "warlock",
    # русские варианты
    "паладин", "маг", "воин", "охотник на демонов", "рыцарь смерти", "шаман",
    "друид", "охотник", "жрец", "разбойник", "чернокнижник",
}
# Файл состояния «постинг приостановлен» (читается/пишется админом через бота)
POSTING_PAUSED_FILE = getattr(config, "HSGURU_SEEN_PATH", BASE_DIR / "cache/hsguru_seen.json").parent / "hsguru_posting_paused.json"


# ============================================================================
# POSTING PAUSE STATE (admin stop/resume)
# ============================================================================

def get_posting_paused() -> bool:
    """Возвращает True, если постинг колод приостановлен администратором."""
    if not POSTING_PAUSED_FILE.exists():
        return False
    try:
        with open(POSTING_PAUSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("paused", False))
    except Exception:
        return False


def set_posting_paused(paused: bool) -> None:
    """Включает или выключает приостановку постинга колод (для админ-панели)."""
    POSTING_PAUSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSTING_PAUSED_FILE, "w", encoding="utf-8") as f:
        json.dump({"paused": paused}, f, ensure_ascii=False)


# ============================================================================
# ARCHETYPE TRANSLATION
# ============================================================================

def load_archetypes() -> Dict[str, str]:
    """Загружает таблицу перевода архетипов (English -> Russian)."""
    translations = {}
    
    if not ARCHETYPES_CSV.exists():
        print(f"[HSGuru] Файл архетипов не найден: {ARCHETYPES_CSV}")
        return translations
    
    try:
        with open(ARCHETYPES_CSV, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(",,") or "Англ. названия" in line:
                    continue
                
                parts = line.split(",")
                if len(parts) >= 3:
                    eng_name = parts[1].strip().strip('"')
                    rus_name = parts[2].strip().strip('"')
                    
                    if eng_name and rus_name:
                        translations[eng_name.lower()] = rus_name
        
        print(f"[HSGuru] Загружено {len(translations)} переводов архетипов")
    except Exception as e:
        print(f"[HSGuru] Ошибка загрузки архетипов: {e}")
    
    return translations


def translate_deck_name(name: str, archetypes: Dict[str, str]) -> str:
    """Переводит название колоды с английского на русский."""
    if not name or not archetypes:
        return name
    
    name_lower = name.lower().strip()
    
    # Точное совпадение
    if name_lower in archetypes:
        return archetypes[name_lower]
    
    # Частичное совпадение - ищем самый длинный подходящий архетип
    best_match = None
    best_length = 0
    
    for eng, rus in archetypes.items():
        if eng in name_lower and len(eng) > best_length:
            best_match = rus
            best_length = len(eng)
    
    if best_match:
        return best_match
    
    return name


def get_archetypes_list() -> List[Tuple[str, str]]:
    """Возвращает список пар (англ. название, рус. название) для отображения в боте."""
    result = []
    if not ARCHETYPES_CSV.exists():
        return result
    try:
        with open(ARCHETYPES_CSV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 3 and row[1].strip() and row[2].strip():
                    eng = row[1].strip().strip('"')
                    rus = row[2].strip().strip('"')
                    if eng and rus and "Англ. названия" not in eng:
                        result.append((eng, rus))
    except Exception as e:
        print(f"[HSGuru] Ошибка чтения архетипов: {e}")
    return result


def add_archetype(eng: str, rus: str) -> bool:
    """Добавляет новый перевод архетипа (англ. -> рус.) в CSV. Возвращает True при успехе."""
    if not eng or not rus:
        return False
    eng = eng.strip()
    rus = rus.strip()
    ARCHETYPES_CSV.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(ARCHETYPES_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["", eng, rus])
        return True
    except Exception as e:
        print(f"[HSGuru] Ошибка добавления архетипа: {e}")
        return False


def update_archetype(eng: str, new_rus: str) -> bool:
    """Обновляет русский перевод архетипа по английскому названию. Возвращает True при успехе."""
    if not eng or not new_rus:
        return False
    eng = eng.strip()
    new_rus = new_rus.strip()
    eng_lower = eng.lower()
    if not ARCHETYPES_CSV.exists():
        return False
    try:
        with open(ARCHETYPES_CSV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        for i, row in enumerate(rows):
            if len(row) >= 3 and row[1].strip().lower() == eng_lower:
                row[2] = new_rus
                rows[i] = row
                break
        else:
            return False
        with open(ARCHETYPES_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        return True
    except Exception as e:
        print(f"[HSGuru] Ошибка обновления архетипа: {e}")
        return False


# ============================================================================
# SEEN DECKS TRACKING
# ============================================================================

def load_seen() -> Dict:
    """Загружает данные о опубликованных колодах (коды и список карт)."""
    seen_path = config.HSGURU_SEEN_PATH
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    
    if seen_path.exists():
        try:
            with open(seen_path, "r") as f:
                data = json.load(f)
                # Поддержка старого формата (просто список кодов)
                if isinstance(data, list):
                    return {
                        "codes": set(data),
                        "decks": {},  # deck_code -> {"cards": set(dbf_ids), "published_at": timestamp}
                        "last_published_format": ""  # Инициализируем пустым для старого формата
                    }
                # Новый формат
                loaded_data = {
                    "codes": set(data.get("codes", [])),
                    "decks": {k: {
                        "cards": set(v.get("cards", [])),
                        "published_at": v.get("published_at"),
                        "format": v.get("format", ""),
                        "name": v.get("name", ""),  # Название колоды для проверки дубликатов
                    } for k, v in data.get("decks", {}).items()},
                    "last_published_format": data.get("last_published_format", "")
                }
                print(f"[HSGuru] 🔍 ДИАГНОСТИКА load_seen: Загружен last_published_format = '{loaded_data['last_published_format']}'")
                return loaded_data
        except Exception as e:
            print(f"[HSGuru] Ошибка загрузки seen: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"[HSGuru] 🔍 ДИАГНОСТИКА load_seen: Файл не существует или ошибка, возвращаем пустые данные")
    return {"codes": set(), "decks": {}, "last_published_format": ""}


def save_seen(seen_data: Dict):
    """Сохраняет данные о опубликованных колодах."""
    seen_path = config.HSGURU_SEEN_PATH
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Конвертируем sets в lists для JSON
    last_format = seen_data.get("last_published_format", "")
    data = {
        "codes": list(seen_data["codes"]),
        "decks": {
            k: {
                "cards": list(v.get("cards") or []),
                "published_at": v.get("published_at", ""),
                "format": v.get("format", ""),
                "name": v.get("name", ""),  # Название колоды для проверки дубликатов
            } for k, v in seen_data["decks"].items()
        },
        "last_published_format": last_format  # Режим последней опубликованной колоды
    }
    
    print(f"[HSGuru] 🔍 ДИАГНОСТИКА save_seen: Сохраняем last_published_format = '{last_format}'")
    
    with open(seen_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_deck_cards_set(deck_code: str) -> Set[int]:
    """Извлекает множество dbfId карт из кода колоды."""
    try:
        deck_parts = deckstrings.parse_deckstring(deck_code)
        cards = deck_parts[0]  # [(dbf_id, count), ...]
        # Собираем все dbfId (без учета количества)
        card_ids = {dbf_id for dbf_id, _ in cards}
        
        # Добавляем карты из сайдбордов
        if len(deck_parts) > 3:
            sideboards = deck_parts[3]
            for item in sideboards:
                if len(item) >= 3:
                    card_id = item[0]
                    card_ids.add(card_id)
        
        return card_ids
    except Exception as e:
        print(f"[HSGuru] Ошибка извлечения карт из {deck_code[:20]}: {e}")
        return set()


def get_statistics_24h() -> Dict:
    """
    Получает статистику опубликованных колод за последние 24 часа.
    
    Returns:
        Словарь с статистикой: {"total": int, "period": str, "last_24h_start": str, "now": str}
    """
    seen_data = load_seen()
    now = datetime.now()
    last_24h = now - timedelta(hours=24)
    
    total = 0
    
    for deck_code, deck_info in seen_data.get("decks", {}).items():
        published_at_str = deck_info.get("published_at")
        if not published_at_str:
            continue
        
        try:
            published_at = datetime.fromisoformat(published_at_str)
            if published_at >= last_24h:
                total += 1
        except Exception:
            continue
    
    return {
        "total": total,
        "period": "24 часа",
        "last_24h_start": last_24h.isoformat(),
        "now": now.isoformat()
    }


def calculate_deck_similarity(cards1: Set[int], cards2: Set[int]) -> float:
    """Рассчитывает похожесть колод (0.0 - 1.0)."""
    if not cards1 or not cards2:
        return 0.0
    
    intersection = len(cards1 & cards2)
    union = len(cards1 | cards2)
    
    if union == 0:
        return 0.0
    
    # Jaccard similarity (коэффициент Жаккара)
    return intersection / union


def is_duplicate_deck(
    deck_code: str,
    deck_cards: Set[int],
    seen_data: Dict,
    deck_name: str = "",
    similarity_threshold: float = 0.90,
) -> bool:
    """
    Проверяет, является ли колода дубликатом.

    Критерии дубликата (достаточно одного):
    1. Точное совпадение кода колоды.
    2. Схожесть набора карт >= similarity_threshold (Jaccard).
    3. Совпадение названия колоды — если название НЕ является generic-именем класса
       (например, просто «Paladin» или «Шаман» — не считается дубликатом по имени).

    Args:
        deck_code: Код колоды
        deck_cards: Множество dbfId карт колоды
        seen_data: Данные об опубликованных колодах
        deck_name: Название колоды для дополнительной проверки
        similarity_threshold: Порог похожести карт (0.90 = 90%)

    Returns:
        True если колода считается дубликатом
    """
    # 1. Точное совпадение кода
    if deck_code in seen_data["codes"]:
        return True

    name_lower = deck_name.strip().lower() if deck_name else ""
    # Проверять по имени имеет смысл только если имя непустое и не generic
    check_by_name = bool(name_lower) and name_lower not in GENERIC_DECK_NAMES

    # 2. Проверяем по картам и по имени
    for existing_code, existing_data in seen_data["decks"].items():
        # Проверка по схожести карт
        existing_cards = existing_data.get("cards", set())
        similarity = calculate_deck_similarity(deck_cards, existing_cards)
        if similarity >= similarity_threshold:
            print(
                f"[HSGuru] Обнаружен дубликат (карты): {deck_code[:20]}... "
                f"похож на {existing_code[:20]}... (схожесть: {similarity:.1%})"
            )
            return True

        # Проверка по названию (только для не-generic имён)
        if check_by_name:
            existing_name = (existing_data.get("name") or "").strip().lower()
            if existing_name and existing_name == name_lower:
                print(
                    f"[HSGuru] Обнаружен дубликат (название): «{deck_name}» "
                    f"уже опубликована как {existing_code[:20]}..."
                )
                return True

    return False


# ============================================================================
# PARSING
# ============================================================================

def fetch_html() -> str:
    """Получает HTML страницы HSGuru (с обходом Cloudflare)."""
    scraper = cloudscraper.create_scraper()
    response = scraper.get(config.HSGURU_URL, timeout=30)
    response.raise_for_status()
    return response.text


def _extract_legend_rank(peak_value: str) -> str:
    """Возвращает числовой ранг легенды из значения Peak, если есть."""
    if not peak_value:
        return ""
    match = re.search(r'\d+', peak_value.replace(',', ''))
    if not match:
        return ""
    try:
        rank = int(match.group(0))
    except (ValueError, TypeError):
        return ""
    return str(rank) if rank > 0 else ""


def parse_decks(html: str, archetypes: Dict[str, str]) -> List[Dict]:
    """
    Парсит колоды из HTML страницы HSGuru.
    
    Структура таблицы:
    Колонка 0: Deck (название + код)
    Колонка 1: Streamer
    Колонка 2: Format
    Колонка 3: Peak
    Колонка 4: Latest
    Колонка 5: Worst
    Колонка 6: Win - Loss (статистика)
    Колонка 7: Links
    Колонка 8: Last Played
    """
    soup = BeautifulSoup(html, "html.parser")
    decks = []
    
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        
        try:
            # Название колоды
            deck_name_en = ""
            deck_link = row.select_one('a[href^="/deck/"]')
            if deck_link:
                deck_name_en = deck_link.get_text(strip=True)
            
            # Переводим на русский
            deck_name = translate_deck_name(deck_name_en, archetypes)
            
            # Стример (колонка 1)
            streamer = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            
            # Формат (колонка 2)
            format_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            
            # Peak, Latest, Worst (колонки 3-5)
            peak = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            latest = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            worst = cells[5].get_text(strip=True) if len(cells) > 5 else ""
            legend_rank = _extract_legend_rank(peak)
            
            # Статистика Win-Loss (колонка 6)
            wins = 0
            losses = 0
            win_loss_text = ""
            if len(cells) > 6:
                win_loss_text = cells[6].get_text(strip=True)
                # Парсим формат "235 - 151" или "2-5"
                match = re.match(r'(\d+)\s*-\s*(\d+)', win_loss_text)
                if match:
                    wins = int(match.group(1))
                    losses = int(match.group(2))
            
            # Last Played (колонка 8)
            last_played = cells[8].get_text(strip=True) if len(cells) > 8 else ""
            
            # Код колоды
            clip_elem = row.select_one("[data-clipboard-text]")
            deck_code = ""
            if clip_elem:
                deck_code = clip_elem.get("data-clipboard-text", "")
            
            if not deck_code or not deck_name:
                continue
            
            decks.append({
                "deck_name": deck_name,
                "deck_name_en": deck_name_en,
                "streamer": streamer,
                "deck_code": deck_code,
                "format": format_cell,
                # Статистика
                "wins": wins,
                "losses": losses,
                "win_loss": win_loss_text,  # Оригинальный текст "235 - 151"
                "total_games": wins + losses,
                # Ранги
                "peak": peak,
                "latest": latest,
                "worst": worst,
                "legend_rank": legend_rank,
                # Дополнительно
                "last_played": last_played,
            })
        except Exception as e:
            print(f"[HSGuru] Ошибка парсинга строки: {e}")
            continue
    
    return decks


# ============================================================================
# MAIN LOOP
# ============================================================================

async def send_daily_statistics(statistics_callback: Optional[Callable] = None):
    """
    Отправляет ежедневную статистику администраторам.
    Вызывается автоматически в 9:00 по Варшаве.
    
    Args:
        statistics_callback: Функция для отправки статистики (принимает текст сообщения и parse_mode)
    """
    if not statistics_callback:
        return
    
    stats = get_statistics_24h()
    
    # Получаем текущее время по Варшаве для отображения
    if ZoneInfo:
        warsaw_tz = ZoneInfo("Europe/Warsaw")
        now_warsaw = datetime.now(warsaw_tz)
        time_str = now_warsaw.strftime('%Y-%m-%d %H:%M:%S %Z')
    else:
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Формируем сообщение
    stats_text = (
        "📊 <b>Ежедневная статистика публикации колод</b>\n\n"
        f"⏰ <b>Период:</b> последние 24 часа\n"
        f"📦 <b>Опубликовано колод:</b> {stats['total']}\n\n"
        f"🕘 Время отчета: {time_str}"
    )
    
    try:
        # Вызываем callback с parse_mode явно
        if asyncio.iscoroutinefunction(statistics_callback):
            await statistics_callback(stats_text, parse_mode='HTML')
        else:
            statistics_callback(stats_text, parse_mode='HTML')
        print(f"[HSGuru] ✓ Статистика отправлена: {stats['total']} колод за 24 часа")
    except Exception as e:
        print(f"[HSGuru] Ошибка отправки статистики: {e}")
        import traceback
        traceback.print_exc()


async def daily_statistics_scheduler(statistics_callback: Optional[Callable] = None):
    """
    Планировщик ежедневной статистики.
    Отправляет статистику каждый день в 9:00 по Варшаве.
    
    Args:
        statistics_callback: Функция для отправки статистики
    """
    if not statistics_callback or not ZoneInfo:
        if not ZoneInfo:
            print("[HSGuru] ⚠ Статистика отключена: требуется pytz или zoneinfo")
        return
    
    # Часовой пояс Варшавы
    warsaw_tz = ZoneInfo("Europe/Warsaw")
    
    print("[HSGuru] ✓ Планировщик статистики запущен (9:00 по Варшаве)")
    
    # Вычисляем следующее время отправки (9:00 по Варшаве)
    def get_next_report_time():
        now_warsaw = datetime.now(warsaw_tz)
        next_report = now_warsaw.replace(hour=9, minute=0, second=0, microsecond=0)
        
        # Если уже прошло 9:00 сегодня, планируем на завтра
        if next_report <= now_warsaw:
            next_report += timedelta(days=1)
        
        return next_report
    
    # Первая отправка (если уже прошло 9:00, будет завтра)
    next_report = get_next_report_time()
    wait_seconds = (next_report - datetime.now(warsaw_tz)).total_seconds()
    
    print(f"[HSGuru] Следующая отправка статистики: {next_report.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Ждем до времени отправки
    await asyncio.sleep(max(0, wait_seconds))
    
    # Отправляем первую статистику
    await send_daily_statistics(statistics_callback)
    
    # Цикл: каждые 24 часа отправляем статистику
    while True:
        # Ждем до следующего дня (24 часа)
        await asyncio.sleep(24 * 3600)
        
        # Отправляем статистику
        await send_daily_statistics(statistics_callback)
        
        # Обновляем время следующей отправки
        next_report = get_next_report_time()
        print(f"[HSGuru] Следующая отправка статистики: {next_report.strftime('%Y-%m-%d %H:%M:%S %Z')}")


async def run_loop(publish_callback: Callable, statistics_callback: Optional[Callable] = None):
    """
    Основной цикл парсера HSGuru.
    Публикует ВСЕ новые подходящие колоды на сайт (WordPress) при каждой проверке.
    В Telegram-канал публикует строго раз в 2 часа (логика в publish_callback / bot.py).

    Args:
        publish_callback: Асинхронная функция для публикации колоды
                         Принимает dict с полями: deck_code, deck_name, streamer, format
        statistics_callback: Функция для отправки ежедневной статистики (опционально)
    """
    if not config.HSGURU_ENABLED:
        print("[HSGuru] Парсер отключен (HSGURU_ENABLED=0)")
        return

    # Интервал между проверками HSGuru (берём из конфига, по умолчанию 30 мин)
    CHECK_INTERVAL = config.HSGURU_INTERVAL_SECONDS  # 1800 сек = 30 мин

    print(f"[HSGuru] ✓ Парсер запущен")
    print(f"[HSGuru] Режим WordPress: все новые колоды публикуются сразу при каждой проверке")
    print(f"[HSGuru] Режим Telegram канал: строго раз в 2 часа")
    print(f"[HSGuru] Интервал проверки HSGuru: каждые {CHECK_INTERVAL // 60} мин")

    # Запускаем планировщик статистики в фоне
    if statistics_callback:
        asyncio.create_task(daily_statistics_scheduler(statistics_callback))

    # Загружаем архетипы один раз
    archetypes = load_archetypes()

    # Первая проверка через 30 секунд после старта
    await asyncio.sleep(30)

    while True:
        try:
            # Проверка приостановки постинга администратором
            if get_posting_paused():
                print(f"[HSGuru] ⏸ Постинг приостановлен администратором. Следующая проверка через 1 мин...")
                await asyncio.sleep(60)
                continue

            # Публикуем ВСЕ новые подходящие колоды
            count = await check_and_publish_all(publish_callback, archetypes)

            if count > 0:
                print(f"[HSGuru] ✓ За эту проверку опубликовано колод на сайт: {count}")
            else:
                print(f"[HSGuru] Нет новых колод для публикации")

        except Exception as e:
            print(f"[HSGuru] Ошибка в цикле: {e}")
            import traceback
            traceback.print_exc()

        print(f"[HSGuru] Следующая проверка через {CHECK_INTERVAL // 60} мин...")
        await asyncio.sleep(CHECK_INTERVAL)


async def check_and_publish_all(publish_callback: Callable, archetypes: Dict[str, str]) -> int:
    """
    Проверяет новые колоды и публикует ВСЕ подходящие на сайт (WordPress).
    В Telegram-канал публикует только одну раз в 2 часа — это контролируется
    внутри publish_callback (bot.publish_hsguru_deck).

    Returns:
        Количество успешно опубликованных колод за этот вызов
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[HSGuru] === Проверка {timestamp} ===")

    # Загружаем HTML
    try:
        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(None, fetch_html)
    except Exception as e:
        print(f"[HSGuru] Ошибка загрузки страницы: {e}")
        return 0

    # Парсим колоды
    decks = parse_decks(html, archetypes)
    print(f"[HSGuru] Найдено колод на сайте: {len(decks)}")

    if not decks:
        return 0

    # Загружаем уже опубликованные
    seen_data = load_seen()

    MIN_GAMES = 20  # Минимум 20 игр для публикации

    # Восстанавливаем last_published_format если пуст
    last_published_format = seen_data.get("last_published_format", "")
    if not last_published_format and seen_data.get("decks"):
        last_deck = None
        last_date = None
        for deck_code_iter, deck_info in seen_data["decks"].items():
            published_at_str = deck_info.get("published_at", "")
            if published_at_str:
                try:
                    published_at = datetime.fromisoformat(published_at_str)
                    if last_date is None or published_at > last_date:
                        last_date = published_at
                        last_deck = deck_info
                except Exception:
                    continue
        if last_deck and last_deck.get("format"):
            last_published_format = last_deck["format"]
            seen_data["last_published_format"] = last_published_format
            print(f"[HSGuru] Восстановлен last_published_format = '{last_published_format}'")

    print(f"[HSGuru] last_published_format = '{last_published_format}'")

    # --- Собираем все колоды, прошедшие фильтры ---
    filtered_low_games = 0
    filtered_duplicates = 0
    filtered_wild = 0
    candidate_decks = []

    for deck in decks:
        deck_code = deck["deck_code"]

        # Извлекаем карты
        deck_cards = get_deck_cards_set(deck_code)
        if not deck_cards:
            print(f"[HSGuru] ⚠ Не удалось извлечь карты из {deck_code[:20]}...")
            continue

        # Проверка на дубликат (по коду, картам и названию)
        if is_duplicate_deck(deck_code, deck_cards, seen_data, deck_name=deck.get("deck_name", "")):
            filtered_duplicates += 1
            continue

        # Минимум игр
        wins_check = deck.get("wins", 0) or 0
        losses_check = deck.get("losses", 0) or 0
        total_games = deck.get("total_games", 0) or (wins_check + losses_check)
        if total_games < MIN_GAMES:
            filtered_low_games += 1
            print(f"[HSGuru] ⚠ Мало игр ({total_games} < {MIN_GAMES}): '{deck['deck_name'][:40]}'")
            continue

        deck_format = deck.get("format", "")
        deck_mode_normalized = FORMAT_MAP.get(deck_format, deck_format)

        candidate_decks.append({
            **deck,
            "cards": deck_cards,
            "normalized_format": deck_mode_normalized,
            "total_games_calc": total_games,
        })

    print(
        f"[HSGuru] Кандидатов: {len(candidate_decks)} | "
        f"Дубликатов: {filtered_duplicates} | "
        f"Мало игр: {filtered_low_games}"
    )

    if not candidate_decks:
        return 0

    # --- Публикуем все кандидаты, применяя wild-consecutive на лету ---
    published_count = 0

    for deck in candidate_decks:
        deck_code = deck["deck_code"]
        deck_mode_normalized = deck["normalized_format"]

        # Wild-фильтр: не более одной Вольной колоды подряд
        if deck_mode_normalized == "Вольный" and last_published_format == "Вольный":
            filtered_wild += 1
            print(f"[HSGuru] ⚠ Пропускаем Вольную подряд: '{deck['deck_name'][:40]}'")
            continue

        print(f"[HSGuru] ✅ Публикуем [{published_count + 1}]: '{deck['deck_name'][:50]}' ({deck['streamer']}, {deck['format']})")

        payload = {
            "deck_code": deck_code,
            "deck_name": deck["deck_name"],
            "streamer": deck["streamer"],
            "player": deck["streamer"],
            "format": deck["format"],
            "wins": deck.get("wins", 0),
            "losses": deck.get("losses", 0),
            "win_loss": deck.get("win_loss", ""),
            "total_games": deck.get("total_games_calc", 0),
            "peak": deck.get("peak", ""),
            "latest": deck.get("latest", ""),
            "worst": deck.get("worst", ""),
            "last_played": deck.get("last_played", ""),
        }

        try:
            result = await publish_callback(payload)
        except Exception as e:
            print(f"[HSGuru] ✗ Ошибка публикации '{deck['deck_name'][:40]}': {e}")
            import traceback
            traceback.print_exc()
            continue

        if result:
            # Обновляем seen_data сразу после каждой успешной публикации,
            # чтобы следующие колоды в этом же батче видели актуальное состояние
            seen_data["codes"].add(deck_code)
            seen_data["decks"][deck_code] = {
                "cards": deck["cards"],
                "published_at": datetime.now().isoformat(),
                "format": deck_mode_normalized,
                "name": deck.get("deck_name", ""),
            }
            seen_data["last_published_format"] = deck_mode_normalized
            last_published_format = deck_mode_normalized  # Обновляем локальную переменную для wild-фильтра
            save_seen(seen_data)

            published_count += 1
            print(f"[HSGuru] ✓ Опубликовано на сайт: '{deck['deck_name'][:50]}'")
        else:
            print(f"[HSGuru] ✗ Ошибка публикации: '{deck['deck_name'][:40]}'")

    if filtered_wild > 0:
        print(f"[HSGuru] Пропущено Вольных подряд: {filtered_wild}")

    return published_count


def get_all_decks_with_status() -> List[Dict]:
    """
    Возвращает ВСЕ колоды, которые бот видит на HSGuru, с оценкой статуса каждой.

    Каждый элемент списка содержит:
        deck_name, streamer, format, wins, losses, total_games, deck_code,
        approved  — True если уже опубликована,
        published_at — дата публикации (str ISO или None),
        rejection_reason — причина отказа (str или None если одобрена/ожидает),
        status — "approved" | "rejected" | "pending"
    """
    MIN_GAMES = 20

    try:
        html = fetch_html()
    except Exception as e:
        print(f"[HSGuru] get_all_decks_with_status: ошибка загрузки страницы: {e}")
        return []

    archetypes = load_archetypes()
    decks = parse_decks(html, archetypes)
    seen_data = load_seen()

    # Восстанавливаем last_published_format
    last_published_format = seen_data.get("last_published_format", "")
    if not last_published_format and seen_data.get("decks"):
        last_deck_info = None
        last_date = None
        for dk_code, dk_info in seen_data["decks"].items():
            pa = dk_info.get("published_at", "")
            if pa:
                try:
                    dt = datetime.fromisoformat(pa)
                    if last_date is None or dt > last_date:
                        last_date = dt
                        last_deck_info = dk_info
                except Exception:
                    pass
        if last_deck_info and last_deck_info.get("format"):
            last_published_format = last_deck_info["format"]

    result = []
    # Чтобы корректно считать wild-consecutive для pending-колод,
    # отслеживаем «последний формат» при симуляции публикаций
    simulated_last_format = last_published_format

    for deck in decks:
        deck_code = deck["deck_code"]
        wins = deck.get("wins", 0) or 0
        losses = deck.get("losses", 0) or 0
        total_games = deck.get("total_games", 0) or (wins + losses)
        deck_format = deck.get("format", "")
        deck_mode = FORMAT_MAP.get(deck_format, deck_format)
        deck_name = deck.get("deck_name", "")

        # --- Уже опубликована? ---
        if deck_code in seen_data["codes"]:
            pub_info = seen_data["decks"].get(deck_code, {})
            result.append({
                "deck_name": deck_name,
                "streamer": deck.get("streamer", ""),
                "format": deck_format,
                "deck_mode": deck_mode,
                "wins": wins,
                "losses": losses,
                "total_games": total_games,
                "deck_code": deck_code,
                "approved": True,
                "published_at": pub_info.get("published_at"),
                "rejection_reason": None,
                "status": "approved",
            })
            continue

        # --- Извлекаем карты (нужно для дубликата по картам) ---
        deck_cards = get_deck_cards_set(deck_code)

        # --- Дубликат по коду (уже проверено выше, но оставим для явности) ---
        rejection_reason = None

        if deck_cards:
            # Дубликат по картам
            for ex_code, ex_data in seen_data["decks"].items():
                ex_cards = ex_data.get("cards", set())
                sim = calculate_deck_similarity(deck_cards, ex_cards)
                if sim >= 0.90:
                    rejection_reason = f"Дубликат по картам ({sim:.0%} схожесть)"
                    break

            # Дубликат по названию
            if rejection_reason is None:
                name_lower = deck_name.strip().lower()
                if name_lower and name_lower not in GENERIC_DECK_NAMES:
                    for ex_code, ex_data in seen_data["decks"].items():
                        ex_name = (ex_data.get("name") or "").strip().lower()
                        if ex_name and ex_name == name_lower:
                            rejection_reason = f"Дубликат по названию «{deck_name}»"
                            break
        else:
            rejection_reason = "Не удалось прочитать карты"

        # --- Мало игр ---
        if rejection_reason is None and total_games < MIN_GAMES:
            rejection_reason = f"Мало игр ({total_games} < {MIN_GAMES})"

        # --- Вольная подряд (используем simulated_last_format) ---
        if rejection_reason is None and deck_mode == "Вольный" and simulated_last_format == "Вольный":
            rejection_reason = "Вольная колода подряд"

        if rejection_reason:
            status = "rejected"
        else:
            status = "pending"
            # Симулируем «публикацию» чтобы следующие pending-колоды
            # правильно видели wild-consecutive
            simulated_last_format = deck_mode

        result.append({
            "deck_name": deck_name,
            "streamer": deck.get("streamer", ""),
            "format": deck_format,
            "deck_mode": deck_mode,
            "wins": wins,
            "losses": losses,
            "total_games": total_games,
            "deck_code": deck_code,
            "approved": False,
            "published_at": None,
            "rejection_reason": rejection_reason,
            "status": status,
        })

    return result


# Обратная совместимость: check_and_publish_one вызывает check_and_publish_all
async def check_and_publish_one(publish_callback: Callable, archetypes: Dict[str, str]) -> bool:
    count = await check_and_publish_all(publish_callback, archetypes)
    return count > 0
