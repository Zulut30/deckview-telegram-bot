#!/usr/bin/env python3
"""
Улучшенный скрипт для проверки статистики колод
Парсит данные с сайта HSGuru.com для сравнения и поиска статистики
"""

import sys
import re
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

try:
    import config
    from wordpress import WordPressClient
    import cloudscraper
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    sys.exit(1)


def parse_hsguru_table() -> List[Dict]:
    """Парсит таблицу колод с сайта HSGuru.com"""
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(config.HSGURU_URL, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        decks = []
        
        # Ищем таблицу с колодами
        table = soup.select_one("table tbody")
        if not table:
            print("   [HSGuru] Таблица не найдена на странице")
            return decks
        
        rows = table.select("tr")
        print(f"   [HSGuru] Найдено строк в таблице: {len(rows)}")
        
        for row_idx, row in enumerate(rows[:20], 1):  # Первые 20 строк
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            
            try:
                # Название колоды
                deck_link = row.select_one('a[href^="/deck/"]')
                deck_name = deck_link.get_text(strip=True) if deck_link else ""
                
                # Стример (обычно во второй колонке)
                streamer = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                
                # Формат (обычно в третьей колонке)
                format_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                
                # Статистика находится в колонке 6 (Win - Loss)
                stats_text = ""
                if len(cells) > 6:
                    stats_text = cells[6].get_text(strip=True)  # Формат: "2 - 5" или "233-150"
                
                # Также получаем Peak, Latest, Worst если нужно
                peak = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                latest = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                worst = cells[5].get_text(strip=True) if len(cells) > 5 else ""
                
                # Код колоды
                clip_elem = row.select_one("[data-clipboard-text]")
                deck_code = clip_elem.get("data-clipboard-text", "") if clip_elem else ""
                
                if deck_name:
                    # Парсим статистику wins-losses
                    wins = 0
                    losses = 0
                    if stats_text:
                        match = re.match(r'(\d+)\s*-\s*(\d+)', stats_text)
                        if match:
                            wins = int(match.group(1))
                            losses = int(match.group(2))
                    
                    decks.append({
                        "name": deck_name,
                        "streamer": streamer,
                        "format": format_cell,
                        "stats": stats_text,
                        "wins": wins,
                        "losses": losses,
                        "total_games": wins + losses,
                        "peak": peak,
                        "latest": latest,
                        "worst": worst,
                        "code": deck_code[:20] + "..." if len(deck_code) > 20 else deck_code,
                        "row": row_idx
                    })
            except Exception as e:
                continue
        
        return decks
    except Exception as e:
        print(f"   [HSGuru] Ошибка парсинга: {e}")
        return []


def check_decks_stats_enhanced(limit: int = 20):
    """Улучшенная проверка статистики колод"""
    print("=" * 80)
    print("Улучшенная проверка статистики колод")
    print("=" * 80)
    print()
    
    if not config.WP_BASE_URL:
        print("❌ WordPress не настроен")
        return
    
    print(f"1. Подключение к WordPress: {config.WP_BASE_URL}")
    client = WordPressClient()
    
    test_result = client.test_connection()
    if not test_result.get("success"):
        print(f"❌ Ошибка подключения: {test_result.get('error')}")
        return
    
    print(f"✅ Подключено как: {test_result.get('user')}")
    print()
    
    # Получаем колоды из WordPress
    print(f"2. Загрузка последних {limit} колод из WordPress...")
    result = client._request(
        "GET",
        f"/wp-json/wp/v2/hs_deck?per_page={limit}&orderby=date&order=desc"
    )
    
    if not result.get("success"):
        print(f"❌ Ошибка: {result.get('error')}")
        return
    
    wp_decks = result.get("data", [])
    print(f"✅ Найдено колод в WordPress: {len(wp_decks)}")
    print()
    
    # Парсим данные с HSGuru
    print("3. Парсинг данных с сайта HSGuru.com...")
    hsguru_decks = parse_hsguru_table()
    print(f"✅ Найдено колод на HSGuru: {len(hsguru_decks)}")
    print()
    
    print("=" * 80)
    print("СРАВНЕНИЕ ДАННЫХ")
    print("=" * 80)
    print()
    
    # Создаем словари для поиска: по названию и по коду
    hsguru_by_name = {}
    hsguru_by_code = {}
    for deck in hsguru_decks:
        name_lower = deck["name"].lower().strip()
        if name_lower not in hsguru_by_name:
            hsguru_by_name[name_lower] = []
        hsguru_by_name[name_lower].append(deck)
        
        # Также индексируем по коду (первые 20 символов)
        if deck["code"]:
            code_key = deck["code"][:20].lower()
            if code_key not in hsguru_by_code:
                hsguru_by_code[code_key] = []
            hsguru_by_code[code_key].append(deck)
    
    # Показываем примеры данных с HSGuru
    print("Примеры данных с HSGuru (первые 5 колод):")
    for deck in hsguru_decks[:5]:
        print(f"  - {deck['name']}")
        print(f"    Стример: {deck['streamer']}, Формат: {deck['format']}")
        if deck['stats']:
            print(f"    Статистика: {deck['stats']} ({deck['total_games']} игр)")
        print()
    
    # Анализируем колоды из WordPress
    missing_streamer = 0
    missing_stats = 0
    duplicates = []
    previous_name = None
    
    for i, deck in enumerate(wp_decks[:10], 1):  # Показываем первые 10
        deck_id = deck.get("id")
        title_obj = deck.get("title", {})
        deck_name = title_obj.get("rendered", title_obj.get("raw", "Без названия")) if isinstance(title_obj, dict) else str(title_obj)
        date = deck.get("date", "")[:10]
        
        # Получаем мета-поля через отдельный запрос
        meta_result = client._request("GET", f"/wp-json/wp/v2/hs_deck/{deck_id}?context=edit")
        meta = {}
        if meta_result.get("success"):
            meta = meta_result.get("data", {}).get("meta", {})
        
        streamer = meta.get("_deck_streamer", "") or "-"
        deck_code_wp = meta.get("_deck_code", "")
        
        if streamer == "-":
            missing_streamer += 1
        
        # Ищем соответствующую колоду на HSGuru
        # Сначала по коду колоды (самый надежный способ)
        hsguru_match = None
        if deck_code_wp:
            code_key = deck_code_wp[:20].lower()
            if code_key in hsguru_by_code:
                hsguru_match = hsguru_by_code[code_key][0]
        
        # Если не нашли по коду, ищем по названию
        if not hsguru_match:
            deck_name_lower = deck_name.lower().strip()
            # Пробуем найти частичное совпадение
            for hsguru_deck in hsguru_decks:
                hsguru_name_lower = hsguru_deck["name"].lower().strip()
                # Проверяем, содержит ли одно название другое
                if deck_name_lower in hsguru_name_lower or hsguru_name_lower in deck_name_lower:
                    hsguru_match = hsguru_deck
                    break
        
        print(f"{i}. ID: {deck_id} - {deck_name}")
        print(f"   Дата: {date}")
        print(f"   WordPress стример: {streamer}")
        
        if hsguru_match:
            print(f"   ✅ Найдено на HSGuru:")
            print(f"      Стример: {hsguru_match['streamer']}")
            print(f"      Формат: {hsguru_match['format']}")
            if hsguru_match['stats']:
                print(f"      Статистика: {hsguru_match['stats']} ({hsguru_match['wins']} побед, {hsguru_match['losses']} поражений)")
                print(f"      Всего игр: {hsguru_match['total_games']}")
                if hsguru_match['total_games'] < 3:
                    print(f"      ⚠ Мало игр (<3) - будет отфильтровано!")
            else:
                print(f"      Статистика: не найдена на сайте")
                missing_stats += 1
            
            # Предложения по обновлению
            suggestions = []
            if streamer == "-" and hsguru_match['streamer']:
                suggestions.append(f"обновить стримера на '{hsguru_match['streamer']}'")
            if hsguru_match['total_games'] > 0:
                suggestions.append(f"добавить статистику: wins={hsguru_match['wins']}, losses={hsguru_match['losses']}")
            if suggestions:
                print(f"   💡 Предложения: {', '.join(suggestions)}")
        else:
            print(f"   ⚠ Не найдено на HSGuru")
            missing_stats += 1
        
        # Проверка дубликатов
        normalized_name = deck_name.lower().strip()
        if previous_name == normalized_name:
            duplicates.append((deck_id, deck_name))
            print(f"   ⚠ ДУБЛИКАТ предыдущей колоды!")
        
        previous_name = normalized_name
        
        # Показываем мета-поля если есть
        if meta:
            print(f"   Мета-поля:")
            for key, value in sorted(meta.items()):
                if value:
                    print(f"      {key}: {value}")
        
        print()
    
    # Итоговая статистика
    print("=" * 80)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"Всего проверено колод: {min(10, len(wp_decks))}")
    print(f"Без стримера: {missing_streamer}")
    print(f"Без статистики: {missing_stats}")
    print(f"Дубликаты: {len(duplicates)}")
    if duplicates:
        print("\nДубликаты:")
        for deck_id, name in duplicates:
            print(f"  - ID {deck_id}: {name}")
    print()
    print("РЕКОМЕНДАЦИИ:")
    print("1. Данные о стримере и статистике можно получить с сайта HSGuru.com")
    print("2. Нужно добавить парсинг статистики из таблицы HSGuru при публикации")
    print("3. Обновить мета-поля _deck_streamer, _deck_wins, _deck_losses в WordPress")
    print("=" * 80)


if __name__ == "__main__":
    try:
        limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
        check_decks_stats_enhanced(limit)
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
