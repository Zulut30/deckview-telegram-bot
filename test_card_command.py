"""
Тест команды /card: поиск карты по названию или id и сохранение изображения на обоях.
Запуск: python test_card_command.py "Название карты"
        python test_card_command.py 12345
        python test_card_command.py CORE_CS3_027
Результат: test_card_output.png (первая из найденных карт) и список совпадений в консоль.
"""
import os
import sys

# Рабочая директория — корень проекта (как в main.py)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import HSJSON_CARDS_URL
from framework.hearthstonejson_api import configure as hsjson_configure, find_cards_by_query, get_card_by_dbfid
from image_creator.card_image import build_card_image, get_card_art_image


def main():
    if len(sys.argv) < 2:
        print("Использование: python test_card_command.py <название карты или id>")
        print('  Пример: python test_card_command.py "Терран"')
        print("  Пример: python test_card_command.py 12345")
        sys.exit(1)
    query = " ".join(sys.argv[1:]).strip()
    hsjson_configure(HSJSON_CARDS_URL)
    matches = find_cards_by_query(query)
    if not matches:
        print("Карта не найдена.")
        sys.exit(1)
    print(f"Найдено карт: {len(matches)}")
    for i, card in enumerate(matches[:10], 1):
        print(f"  {i}. {card.get('name')} (dbfId: {card.get('id')})")
    if len(matches) > 10:
        print(f"  ... и ещё {len(matches) - 10}")
    # Берём первую карту и сохраняем изображение
    card = matches[0]
    dbf_id = card.get("id")
    art = get_card_art_image(card)
    if not art:
        print("Не удалось загрузить арт карты.")
        sys.exit(1)
    image = build_card_image(art)
    out_path = "test_card_output.png"
    image.save(out_path, format="PNG")
    print(f"Изображение сохранено: {out_path} ({card.get('name')})")


if __name__ == "__main__":
    main()
