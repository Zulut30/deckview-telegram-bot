#!/usr/bin/env python3
"""
Скрипт для проверки работоспособности всех компонентов системы.
Запускать перед началом работы бота или при проблемах.
"""
import sys
import os
from pathlib import Path
import json

# Цвета для вывода
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠{Colors.END} {msg}")

def print_success(msg):
    print(f"{Colors.GREEN}✓{Colors.END} {msg}")

def print_error(msg):
    print(f"{Colors.RED}✗{Colors.END} {msg}")

def print_info(msg):
    print(f"{Colors.BLUE}ℹ{Colors.END} {msg}")

def print_header(msg):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}\n")

# Автоматическая активация виртуального окружения
def activate_venv():
    """Пытается активировать виртуальное окружение если оно есть."""
    script_dir = Path(__file__).parent.absolute()
    venv_path = script_dir / "venv"
    
    if venv_path.exists():
        # Добавляем venv в путь
        venv_python = venv_path / "bin" / "python3"
        if venv_python.exists():
            # Вставляем путь к venv в начало sys.path
            venv_lib = venv_path / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            if venv_lib.exists():
                sys.path.insert(0, str(venv_lib))
                return True
            # Альтернативный путь для некоторых систем
            for lib_path in venv_path.glob("lib/python*/site-packages"):
                if lib_path.exists():
                    sys.path.insert(0, str(lib_path))
                    return True
    
    return False

# Пытаемся активировать venv
venv_activated = activate_venv()
if venv_activated:
    print_info("Виртуальное окружение активировано")
else:
    print_warning("Виртуальное окружение не найдено, используются системные пакеты")


def check_config():
    """Проверка конфигурации."""
    print_header("1. ПРОВЕРКА КОНФИГУРАЦИИ")
    
    try:
        import config
        
        # Проверка токена
        if config.BOT_TOKEN:
            print_success(f"BOT_TOKEN установлен (длина: {len(config.BOT_TOKEN)})")
        else:
            print_error("BOT_TOKEN не установлен!")
            return False
        
        # Проверка путей
        print_info(f"IMAGES_PATH: {config.IMAGES_PATH}")
        print_info(f"JSON_PATH: {config.JSON_PATH}")
        print_info(f"JSON_RU_PATH: {config.JSON_RU_PATH}")
        
        # Проверка WordPress
        if config.WP_BASE_URL:
            print_info(f"WordPress настроен: {config.WP_BASE_URL}")
        else:
            print_warning("WordPress не настроен (WP_UPLOAD_ENABLED будет отключен)")
        
        # Проверка HSGuru
        if config.HSGURU_ENABLED:
            print_info(f"HSGuru парсер включен (интервал: {config.HSGURU_INTERVAL_SECONDS} сек)")
        else:
            print_warning("HSGuru парсер отключен")

        # Проверка Blizzard API
        if config.BLIZZARD_ENABLED and config.BLIZZARD_CLIENT_ID and config.BLIZZARD_CLIENT_SECRET:
            print_info(f"Blizzard API включен (регион: {config.BLIZZARD_REGION}, локаль: {config.BLIZZARD_LOCALE})")
        elif config.BLIZZARD_ENABLED:
            print_warning("Blizzard API включен, но ключи не настроены")
        
        # Администраторы
        if config.ADMIN_IDS:
            print_info(f"Администраторы: {len(config.ADMIN_IDS)}")
        else:
            print_warning("Администраторы не настроены")
        
        return True
    except Exception as e:
        print_error(f"Ошибка загрузки конфигурации: {e}")
        return False


def check_files():
    """Проверка наличия необходимых файлов."""
    print_header("2. ПРОВЕРКА ФАЙЛОВ")
    
    errors = []
    warnings = []

    try:
        import config
        blizzard_ok = (
            config.BLIZZARD_ENABLED
            and config.BLIZZARD_CLIENT_ID
            and config.BLIZZARD_CLIENT_SECRET
        )
    except Exception:
        blizzard_ok = False
    
    # Обязательные файлы
    required_files = [
        ("cards.json", "База данных карт (обязательно)" if not blizzard_ok else "База карт (fallback, если Blizzard API недоступен)"),
        ("Архетипы.csv", "Таблица переводов архетипов (рекомендуется)"),
    ]
    
    for file_path, description in required_files:
        path = Path(file_path)
        if path.exists():
            size = path.stat().st_size
            print_success(f"{file_path} ({size:,} байт) - {description}")
        else:
            if file_path == "cards.json" and blizzard_ok:
                print_warning(f"{file_path} не найден - {description}")
                warnings.append(file_path)
            else:
                print_error(f"{file_path} не найден - {description}")
                errors.append(file_path)
    
    # Опциональные файлы
    optional_files = [
        ("cardsRU.json", "Русские названия карт"),
        (".env", "Переменные окружения"),
    ]
    
    for file_path, description in optional_files:
        path = Path(file_path)
        if path.exists():
            print_success(f"{file_path} найден - {description}")
        else:
            print_warning(f"{file_path} не найден - {description}")
            warnings.append(file_path)
    
    return len(errors) == 0


def check_card_database():
    """Проверка базы данных карт."""
    print_header("3. ПРОВЕРКА БАЗЫ ДАННЫХ КАРТ")
    
    try:
        from loader import CardDatabase
        import config
        
        if not config.JSON_PATH.exists():
            if config.BLIZZARD_ENABLED and config.BLIZZARD_CLIENT_ID and config.BLIZZARD_CLIENT_SECRET:
                print_warning(f"Файл {config.JSON_PATH} не найден (используем Blizzard API)")
            else:
                print_error(f"Файл {config.JSON_PATH} не найден")
                return False
        
        print_info("Загрузка базы карт...")
        card_db = CardDatabase(config.JSON_PATH, config.JSON_RU_PATH if config.JSON_RU_PATH.exists() else None)
        
        total_cards = len(card_db.cards)
        print_success(f"Загружено карт: {total_cards:,}")
        
        # Проверка специальных карт
        if card_db.ETC_BAND_MANAGER_DBFID:
            print_success(f"E.T.C., Band Manager найден: dbfId={card_db.ETC_BAND_MANAGER_DBFID}")
        else:
            print_warning("E.T.C., Band Manager не найден")
        
        if card_db.ZILLIAX_DELUXE_3000_DBFID:
            print_success(f"Zilliax Deluxe 3000 найден: dbfId={card_db.ZILLIAX_DELUXE_3000_DBFID}")
        else:
            print_warning("Zilliax Deluxe 3000 не найден")
        
        if card_db.ZILLIAX_MODULE_DBFIDS:
            print_success(f"Модулей Зиллакса найдено: {len(card_db.ZILLIAX_MODULE_DBFIDS)}")
        
        # Тестовый поиск
        test_card = card_db.search_card_by_name("Reno")
        if test_card:
            print_success(f"Тестовый поиск 'Reno': найдено - {test_card['name']}")
        else:
            print_warning("Тестовый поиск 'Reno': не найдено")
        
        return True
    except Exception as e:
        print_error(f"Ошибка загрузки базы карт: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_images():
    """Проверка изображений карт."""
    print_header("4. ПРОВЕРКА ИЗОБРАЖЕНИЙ КАРТ")
    
    try:
        import config
        
        images_path = config.IMAGES_PATH
        
        if not images_path.exists():
            print_error(f"Папка {images_path} не найдена")
            return False
        
        # Подсчет изображений
        image_files = list(images_path.glob("*.png"))
        total_images = len(image_files)
        
        print_success(f"Найдено изображений: {total_images:,}")
        
        if total_images == 0:
            print_error("В папке нет изображений карт!")
            return False
        
        # Проверка размера папки
        total_size = sum(f.stat().st_size for f in image_files)
        size_mb = total_size / (1024 * 1024)
        print_info(f"Общий размер: {size_mb:.1f} MB")
        
        # Проверка доступности нескольких случайных файлов
        import random
        sample = random.sample(image_files, min(5, total_images))
        all_ok = True
        for img_file in sample:
            try:
                from PIL import Image
                img = Image.open(img_file)
                print_success(f"  {img_file.name}: {img.size[0]}x{img.size[1]}")
            except Exception as e:
                print_error(f"  {img_file.name}: ошибка открытия - {e}")
                all_ok = False
        
        return all_ok
    except Exception as e:
        print_error(f"Ошибка проверки изображений: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_database():
    """Проверка базы данных колод."""
    print_header("5. ПРОВЕРКА БАЗЫ ДАННЫХ КОЛОД")
    
    try:
        from database import DeckDatabase
        
        db = DeckDatabase()
        print_success("База данных инициализирована")
        
        # Статистика
        stats = db.get_statistics()
        print_info(f"Всего колод: {stats['total_decks']:,}")
        print_info(f"Колод сегодня: {stats['today_decks']:,}")
        print_info(f"За 7 дней: {stats['week_decks']:,}")
        print_info(f"Лайков: {stats['total_likes']:,}, Дизлайков: {stats['total_dislikes']:,}")
        
        return True
    except Exception as e:
        print_error(f"Ошибка проверки базы данных: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_generator():
    """Проверка генератора изображений."""
    print_header("6. ПРОВЕРКА ГЕНЕРАТОРА ИЗОБРАЖЕНИЙ")
    
    try:
        from loader import CardDatabase
        from generator import DeckImageGenerator
        import config
        
        # Загружаем БД карт
        card_db = CardDatabase(config.JSON_PATH, config.JSON_RU_PATH if config.JSON_RU_PATH.exists() else None)
        
        # Инициализируем генератор
        generator = DeckImageGenerator(card_db, config.IMAGES_PATH)
        print_success("Генератор инициализирован")
        
        # Проверка шрифтов
        if generator.fonts:
            print_success(f"Загружено шрифтов: {len(generator.fonts)}")
        else:
            print_warning("Шрифты не загружены (будет использован default)")
        
        # Проверка ассетов
        if generator.logo_image:
            print_success("Логотип загружен")
        else:
            print_warning("Логотип не найден")
        
        # Тестовая генерация (опционально, может быть долго)
        print_info("Для полной проверки используйте команду /admin → 'Проверить отправку колод'")
        
        return True
    except Exception as e:
        print_error(f"Ошибка проверки генератора: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_archetypes():
    """Проверка таблицы архетипов."""
    print_header("7. ПРОВЕРКА ТАБЛИЦЫ АРХЕТИПОВ")
    
    try:
        from hsguru_scraper import load_archetypes
        
        archetypes = load_archetypes()
        
        if archetypes:
            print_success(f"Загружено переводов: {len(archetypes)}")
            
            # Показываем несколько примеров
            samples = list(archetypes.items())[:3]
            for eng, rus in samples:
                print_info(f"  {eng} → {rus}")
        else:
            print_warning("Таблица архетипов пуста или не найдена")
        
        return True
    except Exception as e:
        print_error(f"Ошибка проверки архетипов: {e}")
        return False


def check_wordpress():
    """Проверка подключения к WordPress."""
    print_header("8. ПРОВЕРКА WORDPRESS (опционально)")
    
    try:
        import config
        
        if not config.WP_BASE_URL:
            print_warning("WordPress не настроен - пропускаем проверку")
            return True
        
        from wordpress import get_client
        
        client = get_client()
        result = client.test_connection()
        
        if result.get("success"):
            print_success(f"WordPress подключен: {result.get('user')}")
            print_info(f"  User ID: {result.get('user_id')}")
            print_info(f"  Roles: {', '.join(result.get('roles', []))}")
            
            # Проверка таксономий
            classes = client.get_taxonomy_terms("deck_class")
            modes = client.get_taxonomy_terms("deck_mode")
            print_info(f"  Классов: {len(classes)}, Режимов: {len(modes)}")
            
            return True
        else:
            print_error(f"Ошибка подключения к WordPress: {result.get('error')}")
            return False
    except Exception as e:
        print_error(f"Ошибка проверки WordPress: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_hsguru_parser():
    """Проверка парсера HSGuru."""
    print_header("9. ПРОВЕРКА ПАРСЕРА HSGURU (опционально)")
    
    try:
        import config
        
        if not config.HSGURU_ENABLED:
            print_warning("HSGuru парсер отключен - пропускаем проверку")
            return True
        
        # Проверка файла seen
        seen_path = config.HSGURU_SEEN_PATH
        if seen_path.exists():
            try:
                with open(seen_path, 'r') as f:
                    seen = json.load(f)
                print_success(f"Файл отслеживания найден: {len(seen)} колод уже опубликовано")
            except:
                print_warning(f"Файл {seen_path} поврежден или пуст")
        else:
            print_info(f"Файл отслеживания не найден (будет создан при первом запуске)")
        
        # Проверка URL
        print_info(f"URL: {config.HSGURU_URL}")
        print_info(f"Интервал: {config.HSGURU_INTERVAL_SECONDS} сек ({config.HSGURU_INTERVAL_SECONDS // 60} мин)")
        
        # Попытка подключения (опционально, может быть долго)
        print_warning("Для проверки подключения используйте команду /post 1")
        
        return True
    except Exception as e:
        print_error(f"Ошибка проверки парсера: {e}")
        return False


def main():
    """Основная функция проверки."""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}  ПРОВЕРКА СИСТЕМЫ TELEGRAM MANACOST BOT{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    results = []
    
    # Выполняем все проверки
    results.append(("Конфигурация", check_config()))
    results.append(("Файлы", check_files()))
    results.append(("База карт", check_card_database()))
    results.append(("Изображения", check_images()))
    results.append(("База данных", check_database()))
    results.append(("Генератор", check_generator()))
    results.append(("Архетипы", check_archetypes()))
    results.append(("WordPress", check_wordpress()))
    results.append(("HSGuru", check_hsguru_parser()))
    
    # Итоговая статистика
    print_header("ИТОГОВЫЙ РЕЗУЛЬТАТ")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        if result:
            print_success(f"{name}")
        else:
            print_error(f"{name}")
    
    print(f"\n{Colors.BOLD}Пройдено проверок: {passed}/{total}{Colors.END}\n")
    
    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ Все проверки пройдены! Система готова к работе.{Colors.END}\n")
        return 0
    else:
        print(f"{Colors.YELLOW}{Colors.BOLD}⚠ Некоторые проверки не пройдены. Проверьте ошибки выше.{Colors.END}\n")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Проверка прервана пользователем{Colors.END}\n")
        sys.exit(1)
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
