#!/usr/bin/env python3
"""
Тестовый скрипт для проверки парсера HSGuru
Показывает все данные, которые извлекаются из таблицы
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import config
    from hsguru_scraper import fetch_html, parse_decks, load_archetypes
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    sys.exit(1)


def test_parser():
    """Тестирует парсер HSGuru"""
    print("=" * 80)
    print("ТЕСТ ПАРСЕРА HSGURU")
    print("=" * 80)
    print()
    
    # Загружаем архетипы для перевода
    print("1. Загрузка архетипов...")
    archetypes = load_archetypes()
    print(f"   ✅ Загружено {len(archetypes)} переводов")
    print()
    
    # Загружаем HTML
    print("2. Загрузка страницы HSGuru...")
    try:
        html = fetch_html()
        print(f"   ✅ HTML загружен ({len(html)} символов)")
    except Exception as e:
        print(f"   ❌ Ошибка загрузки: {e}")
        return
    print()
    
    # Парсим колоды
    print("3. Парсинг колод из таблицы...")
    try:
        decks = parse_decks(html, archetypes)
        print(f"   ✅ Найдено колод: {len(decks)}")
    except Exception as e:
        print(f"   ❌ Ошибка парсинга: {e}")
        import traceback
        traceback.print_exc()
        return
    print()
    
    if not decks:
        print("❌ Колоды не найдены!")
        return
    
    # Показываем первые 10 колод со всеми данными
    print("=" * 80)
    print(f"ПЕРВЫЕ {min(10, len(decks))} КОЛОД СО ВСЕМИ ДАННЫМИ")
    print("=" * 80)
    print()
    
    for i, deck in enumerate(decks[:10], 1):
        print(f"{i}. {deck.get('deck_name', 'N/A')}")
        print(f"   Английское название: {deck.get('deck_name_en', 'N/A')}")
        print(f"   Стример: {deck.get('streamer', 'N/A')}")
        print(f"   Формат: {deck.get('format', 'N/A')}")
        print(f"   Код колоды: {deck.get('deck_code', 'N/A')[:50]}...")
        
        # Статистика
        wins = deck.get('wins', 0)
        losses = deck.get('losses', 0)
        total_games = deck.get('total_games', 0)
        win_loss = deck.get('win_loss', '')
        
        print(f"   Статистика:")
        print(f"     Win-Loss текст: {win_loss if win_loss else 'не найдено'}")
        print(f"     Победы: {wins}")
        print(f"     Поражения: {losses}")
        print(f"     Всего игр: {total_games}")
        
        if total_games > 0:
            if total_games < 10:
                print(f"     ⚠ Мало игр (<10) - будет отфильтровано!")
            else:
                print(f"     ✅ Достаточно игр для показа")
        
        # Ранги
        peak = deck.get('peak', '')
        latest = deck.get('latest', '')
        worst = deck.get('worst', '')
        
        if peak or latest or worst:
            print(f"   Ранги:")
            if peak:
                print(f"     Peak: {peak}")
            if latest:
                print(f"     Latest: {latest}")
            if worst:
                print(f"     Worst: {worst}")
        else:
            print(f"   Ранги: не указаны")
        
        # Last Played
        last_played = deck.get('last_played', '')
        if last_played:
            print(f"   Last Played: {last_played}")
        
        print()
    
    # Статистика по всем колодам
    print("=" * 80)
    print("СТАТИСТИКА ПО ВСЕМ КОЛОДАМ")
    print("=" * 80)
    
    total_decks = len(decks)
    decks_with_stats = sum(1 for d in decks if d.get('total_games', 0) > 0)
    decks_without_stats = total_decks - decks_with_stats
    decks_low_games = sum(1 for d in decks if 0 < d.get('total_games', 0) < 10)
    decks_enough_games = sum(1 for d in decks if d.get('total_games', 0) >= 10)
    
    decks_with_streamer = sum(1 for d in decks if d.get('streamer', '').strip())
    decks_without_streamer = total_decks - decks_with_streamer
    
    decks_with_ranks = sum(1 for d in decks if d.get('peak') or d.get('latest') or d.get('worst'))
    
    print(f"Всего колод: {total_decks}")
    print(f"Со статистикой: {decks_with_stats}")
    print(f"  - Достаточно игр (≥10): {decks_enough_games}")
    print(f"  - Мало игр (<10): {decks_low_games}")
    print(f"Без статистики: {decks_without_stats}")
    print()
    print(f"Со стримером: {decks_with_streamer}")
    print(f"Без стримера: {decks_without_streamer}")
    print()
    print(f"С рангами: {decks_with_ranks}")
    print()
    
    # Примеры колод с разной статистикой
    print("=" * 80)
    print("ПРИМЕРЫ КОЛОД")
    print("=" * 80)
    
    # Колода с большой статистикой
    high_stats = [d for d in decks if d.get('total_games', 0) >= 100]
    if high_stats:
        print("\nКолода с большой статистикой:")
        d = high_stats[0]
        print(f"  {d['deck_name']} - {d['wins']}-{d['losses']} ({d['total_games']} игр)")
    
    # Колода с малой статистикой
    low_stats = [d for d in decks if 0 < d.get('total_games', 0) < 10]
    if low_stats:
        print("\nКолоды с малой статистикой (будут отфильтрованы):")
        for d in low_stats[:3]:  # Показываем первые 3
            print(f"  {d['deck_name']} - {d['wins']}-{d['losses']} ({d['total_games']} игр)")
    
    # Колода без статистики
    no_stats = [d for d in decks if d.get('total_games', 0) == 0]
    if no_stats:
        print("\nКолода без статистики:")
        d = no_stats[0]
        print(f"  {d['deck_name']} - статистика не найдена")
    
    print()
    print("=" * 80)
    print("✅ ТЕСТ ЗАВЕРШЕН")
    print("=" * 80)


if __name__ == "__main__":
    try:
        test_parser()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
