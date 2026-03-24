#!/usr/bin/env python3
"""
Скрипт для проверки сохранения статистики в WordPress
Проверяет последние опубликованные колоды на наличие статистики
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import config
    from wordpress import WordPressClient
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    sys.exit(1)


def check_statistics():
    """Проверяет статистику в последних колодах"""
    print("=" * 80)
    print("ПРОВЕРКА СОХРАНЕНИЯ СТАТИСТИКИ В WORDPRESS")
    print("=" * 80)
    print()
    
    if not config.WP_BASE_URL:
        print("❌ WordPress не настроен")
        return
    
    client = WordPressClient()
    
    # Тестируем подключение
    test_result = client.test_connection()
    if not test_result.get("success"):
        print(f"❌ Ошибка подключения: {test_result.get('error')}")
        return
    
    print(f"✅ Подключено как: {test_result.get('user')}")
    print()
    
    # Получаем последние 10 колод
    print("Загрузка последних 10 колод...")
    result = client._request(
        "GET",
        "/wp-json/wp/v2/hs_deck?per_page=10&orderby=date&order=desc"
    )
    
    if not result.get("success"):
        print(f"❌ Ошибка: {result.get('error')}")
        return
    
    decks = result.get("data", [])
    print(f"✅ Найдено колод: {len(decks)}")
    print()
    
    print("=" * 80)
    print("ПРОВЕРКА СТАТИСТИКИ")
    print("=" * 80)
    print()
    
    no_stats = 0
    has_stats = 0
    low_games = 0
    
    for i, deck in enumerate(decks, 1):
        deck_id = deck.get("id")
        title_obj = deck.get("title", {})
        deck_name = title_obj.get("rendered", "Без названия") if isinstance(title_obj, dict) else str(title_obj)
        
        # Получаем мета-поля через отдельный запрос
        meta_result = client._request("GET", f"/wp-json/wp/v2/hs_deck/{deck_id}?context=edit")
        meta = {}
        if meta_result.get("success"):
            meta = meta_result.get("data", {}).get("meta", {})
        
        streamer = meta.get("_deck_streamer", "") or "-"
        wins = meta.get("_deck_wins", "")
        losses = meta.get("_deck_losses", "")
        
        # Проверяем статистику
        has_statistics = False
        total_games = 0
        
        if wins != '' and losses != '':
            wins_int = int(wins) if wins else 0
            losses_int = int(losses) if losses else 0
            total_games = wins_int + losses_int
            has_statistics = True
            has_stats += 1
            
            if total_games > 0 and total_games < 10:
                low_games += 1
        else:
            no_stats += 1
        
        status = "✅" if has_statistics else "❌"
        if has_statistics and total_games < 10:
            status = "⚠️"
        
        print(f"{i}. {status} ID: {deck_id} - {deck_name}")
        print(f"   Стример: {streamer}")
        if has_statistics:
            print(f"   Статистика: {wins}-{losses} ({total_games} игр)")
            if total_games < 10:
                print(f"   ⚠️ МАЛО ИГР (<10) - должна была быть отфильтрована!")
        else:
            print(f"   Статистика: НЕ НАЙДЕНА")
        print()
    
    # Итоговая статистика
    print("=" * 80)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"Всего проверено: {len(decks)}")
    print(f"Со статистикой: {has_stats}")
    print(f"Без статистики: {no_stats}")
    print(f"С <10 игр (должны быть отфильтрованы): {low_games}")
    print()
    
    if no_stats > 0:
        print("⚠️ ПРОБЛЕМА: Найдены колоды без статистики!")
        print("   Проверьте, что парсер извлекает статистику и она передается в WordPress")
    
    if low_games > 0:
        print("⚠️ ПРОБЛЕМА: Найдены колоды с <10 игр!")
        print("   Они должны были быть отфильтрованы перед публикацией")
    
    print("=" * 80)


if __name__ == "__main__":
    try:
        check_statistics()
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
