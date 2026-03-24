#!/usr/bin/env python3
"""
Скрипт для проверки статистики колод из WordPress
Выводит информацию о последних колодах и их статистике побед/поражений

Использование:
    # Активируйте виртуальное окружение:
    source venv/bin/activate
    
    # Затем запустите скрипт:
    python check_decks_stats.py          # последние 20 колод (по умолчанию)
    python check_decks_stats.py 10      # последние 10 колод
    python check_decks_stats.py 50      # последние 50 колод
"""

import sys
import re
from pathlib import Path

# Добавляем текущую директорию в путь для импорта модулей
sys.path.insert(0, str(Path(__file__).parent))

try:
    import config
    from wordpress import WordPressClient
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    print("Убедитесь, что вы запускаете скрипт из директории tg-manacost-bot")
    sys.exit(1)


def normalize_deck_name(name: str) -> str:
    """Нормализация названия колоды для сравнения."""
    normalized = name.strip().lower()
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def parse_win_loss(value: str) -> tuple[int, int]:
    """Парсинг статистики из строки формата 'wins-losses'."""
    if not value:
        return 0, 0
    
    match = re.match(r'(\d+)\s*-\s*(\d+)', str(value))
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def get_total_games(meta: dict) -> int:
    """Получение общего количества игр из мета-полей."""
    wins = 0
    losses = 0
    
    # Вариант 1: отдельные поля
    wins_meta = meta.get('_deck_wins', '')
    losses_meta = meta.get('_deck_losses', '')
    
    if wins_meta != '' or losses_meta != '':
        wins = int(wins_meta) if wins_meta else 0
        losses = int(losses_meta) if losses_meta else 0
    else:
        # Вариант 2: поле win_loss
        win_loss = meta.get('_deck_win_loss', '')
        if win_loss:
            wins, losses = parse_win_loss(win_loss)
        else:
            # Вариант 3: поле stats
            deck_stats = meta.get('_deck_stats', '')
            if deck_stats:
                wins, losses = parse_win_loss(deck_stats)
    
    return wins + losses


def check_decks_stats(limit: int = 20):
    """Проверка статистики колод."""
    print("=" * 80)
    print("Проверка статистики колод из WordPress")
    print("=" * 80)
    print()
    
    # Проверяем настройки WordPress
    if not config.WP_BASE_URL:
        print("❌ WordPress не настроен (WP_BASE_URL не установлен)")
        return
    
    print(f"Подключение к WordPress: {config.WP_BASE_URL}")
    print()
    
    # Создаем клиент WordPress
    client = WordPressClient()
    
    # Тестируем подключение
    test_result = client.test_connection()
    if not test_result.get("success"):
        print(f"❌ Ошибка подключения: {test_result.get('error')}")
        return
    
    print(f"✅ Подключено как: {test_result.get('user')}")
    print()
    
    # Получаем список колод с включением всех мета-полей
    print(f"Загрузка последних {limit} колод...")
    # Пробуем получить с контекстом 'edit' для доступа ко всем мета-полям
    result = client._request(
        "GET",
        f"/wp-json/wp/v2/hs_deck?per_page={limit}&orderby=date&order=desc&context=edit"
    )
    
    # Если не получилось с edit контекстом, пробуем обычный способ
    if not result.get("success") or not result.get("data"):
        result = client._request(
            "GET",
            f"/wp-json/wp/v2/hs_deck?per_page={limit}&orderby=date&order=desc&_embed"
        )
    
    if not result.get("success"):
        print(f"❌ Ошибка получения колод: {result.get('error')}")
        return
    
    decks = result.get("data", [])
    if not decks:
        print("Колоды не найдены.")
        return
    
    print(f"Найдено колод: {len(decks)}")
    print()
    print("=" * 80)
    print()
    
    # Анализируем колоды
    filtered_count = 0
    duplicate_count = 0
    low_games_count = 0
    previous_name = None
    
    for i, deck in enumerate(decks, 1):
        deck_id = deck.get("id")
        
        # Обрабатываем title (может быть строкой или объектом)
        title_obj = deck.get("title", {})
        if isinstance(title_obj, dict):
            deck_name = title_obj.get("rendered", title_obj.get("raw", "Без названия"))
        else:
            deck_name = str(title_obj) if title_obj else "Без названия"
        
        date = deck.get("date", "")[:10]  # Только дата
        
        # Получаем мета-поля (пробуем разные варианты)
        meta = deck.get("meta", {})
        
        # Если meta пустой, пробуем получить напрямую через отдельный запрос с контекстом edit
        if not meta or len(meta) == 0:
            # Пробуем через обычный endpoint с контекстом edit
            meta_result = client._request("GET", f"/wp-json/wp/v2/hs_deck/{deck_id}?context=edit")
            if meta_result.get("success"):
                full_deck = meta_result.get("data", {})
                meta = full_deck.get("meta", {})
                
                # Если все еще пусто, пробуем кастомный endpoint (если есть)
                if not meta:
                    custom_meta_result = client._request("GET", f"/wp-json/manacost/v1/deck-meta/{deck_id}")
                    if custom_meta_result.get("success"):
                        custom_data = custom_meta_result.get("data", {})
                        if custom_data:
                            meta = custom_data
                
                # Также проверяем другие возможные места в объекте
                if not meta:
                    meta = {}
                    for key in ['_deck_streamer', '_deck_wins', '_deck_losses', '_deck_win_loss', '_deck_stats', 
                               'deck_streamer', 'deck_wins', 'deck_losses', 'deck_win_loss', 'deck_stats']:
                        if key in full_deck and full_deck[key]:
                            meta[key] = full_deck[key]
        
        # Ищем стримера в разных местах
        streamer = (
            meta.get("_deck_streamer") or 
            meta.get("deck_streamer") or
            deck.get("deck_streamer") or
            deck.get("streamer") or
            "-"
        )
        if streamer == "":
            streamer = "-"
        
        # Ищем статистику в разных полях
        wins_meta = meta.get("_deck_wins") or meta.get("deck_wins") or ""
        losses_meta = meta.get("_deck_losses") or meta.get("deck_losses") or ""
        win_loss = meta.get("_deck_win_loss") or meta.get("deck_win_loss") or ""
        deck_stats = meta.get("_deck_stats") or meta.get("deck_stats") or ""
        
        # Показываем ВСЕ мета-поля для отладки (только для первых 3 колод)
        if i <= 3:
            print(f"   [DEBUG] Все мета-поля для ID {deck_id}:")
            if meta:
                for key, value in sorted(meta.items()):
                    if value or value == 0:  # Показываем все значения, включая 0
                        print(f"      {key}: {repr(value)}")
            else:
                print(f"      (мета-поля не найдены или пусты)")
            
            # Также показываем все поля объекта deck
            print(f"   [DEBUG] Все поля объекта deck:")
            for key in sorted(deck.keys()):
                if key not in ['meta', 'title']:  # Эти уже показаны
                    value = deck.get(key)
                    if value:
                        print(f"      {key}: {type(value).__name__}")
            print()
        
        # Вычисляем статистику
        total_games = get_total_games(meta)
        wins = 0
        losses = 0
        
        if wins_meta != '' or losses_meta != '':
            wins = int(wins_meta) if wins_meta else 0
            losses = int(losses_meta) if losses_meta else 0
        elif win_loss:
            wins, losses = parse_win_loss(win_loss)
        elif deck_stats:
            wins, losses = parse_win_loss(deck_stats)
        
        # Проверяем фильтрацию
        normalized_name = normalize_deck_name(deck_name)
        is_duplicate = (previous_name is not None and normalized_name == previous_name)
        is_low_games = (total_games > 0 and total_games < 10)
        will_be_filtered = is_duplicate or is_low_games
        
        if is_duplicate:
            duplicate_count += 1
        if is_low_games:
            low_games_count += 1
        if will_be_filtered:
            filtered_count += 1
        
        previous_name = normalized_name
        
        # Выводим информацию
        status_icon = "❌" if will_be_filtered else "✅"
        status_reason = []
        if is_duplicate:
            status_reason.append("дубликат")
            if is_low_games:
                status_reason.append(f"мало игр ({total_games} < 10)")
        
        print(f"{i}. {status_icon} ID: {deck_id}")
        print(f"   Название: {deck_name}")
        print(f"   Стример: {streamer}")
        print(f"   Дата: {date}")
        print(f"   Статистика:")
        
        # Показываем откуда взялись данные
        if wins_meta != '' or losses_meta != '':
            print(f"     ✓ _deck_wins: {wins}")
            print(f"     ✓ _deck_losses: {losses}")
        elif win_loss:
            print(f"     ✓ _deck_win_loss: '{win_loss}' → {wins}-{losses}")
        elif deck_stats:
            print(f"     ✓ _deck_stats: '{deck_stats}' → {wins}-{losses}")
        else:
            print(f"     ✗ Статистика не найдена")
        
        print(f"     → Всего игр: {total_games if total_games > 0 else 'нет данных'}")
        
        if will_be_filtered:
            print(f"   ⚠ Будет отфильтровано: {', '.join(status_reason)}")
        else:
            print(f"   ✓ Пройдет фильтрацию")
        
        print()
    
    # Итоговая статистика
    print("=" * 80)
    print("ИТОГОВАЯ СТАТИСТИКА ФИЛЬТРАЦИИ")
    print("=" * 80)
    print(f"Всего колод: {len(decks)}")
    print(f"Будут отфильтрованы: {filtered_count}")
    print(f"  - Дубликаты: {duplicate_count}")
    print(f"  - Мало игр (<10): {low_games_count}")
    print(f"Пройдут фильтрацию: {len(decks) - filtered_count}")
    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        # Можно указать количество колод через аргумент командной строки
        if len(sys.argv) > 1:
            try:
                limit = int(sys.argv[1])
                if limit <= 0:
                    print("❌ Количество колод должно быть больше 0")
                    sys.exit(1)
            except ValueError:
                print(f"❌ Неверный аргумент: '{sys.argv[1]}'. Укажите число (например: 10)")
                print("\nИспользование:")
                print("  python check_decks_stats.py          # последние 20 колод")
                print("  python check_decks_stats.py 10       # последние 10 колод")
                print("  python check_decks_stats.py 50       # последние 50 колод")
                sys.exit(1)
        else:
            limit = 20
        
        check_decks_stats(limit)
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
