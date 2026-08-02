"""Проверка создания картинки по коду колоды."""
import asyncio
import sys

# тот же patch, что и в main.py
# from gevent.monkey import patch_all
# patch_all(thread=False, select=False)

from image_creator import create_picture

DECK_CODE = "AAEBAcn1AhCFF/LtA7CRBOWwBOa9BPXOBJbUBIfqBJfvBP3EBebFBfnGBfX4Bab7BamVBse4BgzrrAPnoAT8rAT+tASWtwTcvQTivQSlkgXI6wXu/QXxgAaJmQYAAQP7sAP9xAWIsQP9xAX/4QT9xAUAAA=="


async def main():
    print("Получение данных колоды и создание картинки...")
    try:
        image, cost, deck_class, deck_mode, _ = await create_picture(DECK_CODE, deck_name="Рафаам Чернокнижник\n(Сверхдлинный тестовый заголовок для проверки)")
        print(f"Рассчитанная пыль: {cost}")
        if image is None:
            print("Ошибка: create_picture вернул None (возможно, ошибка API или нет BATTLE_NET_TOKEN в .env)")
            sys.exit(1)
        image.save("test_output.png", format="PNG")
        print("OK: картинка сохранена в test_output.png")
    except Exception as e:
        print(f"Ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
