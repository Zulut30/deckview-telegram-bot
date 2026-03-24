"""
Модуль для генерации изображений колод Hearthstone.
Премиум визуальный стиль с градиентами, тенями и золотыми акцентами.
"""
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import math
import pkg_resources
import config
from loader import CardDatabase, card_name_to_ids

# DEBUGGING: Проверка версии библиотеки hearthstone
try:
    version = pkg_resources.get_distribution("hearthstone").version
    print(f"DEBUG: INSTALLED HEARTHSTONE LIB VERSION: {version}")
except:
    print("DEBUG: Could not detect library version.")


class DeckImageGenerator:
    """Класс для генерации изображений колод с премиум визуальным стилем."""
    
    # Фолбек ID карт-владельцев сайдбордов (на случай, если БД их не определит)
    ETC_ID_FALLBACK = 90749  # E.T.C., Band Manager
    ZILLIAX_ID_FALLBACK = 103501  # Zilliax Deluxe 3000
    
    def __init__(self, card_db: CardDatabase, images_path: Path):
        """
        Инициализация генератора изображений.
        
        Args:
            card_db: Экземпляр базы данных карт
            images_path: Путь к папке с изображениями карт
        """
        self.card_db = card_db
        self.images_path = images_path
        self.base_path = Path(__file__).resolve().parent
        self.card_width = config.CARD_WIDTH
        self.card_height = config.CARD_HEIGHT
        self.cards_per_row = config.CARDS_PER_ROW
        self.main_cards_per_row = 7  # Больше карт в ряду для основной колоды
        self.padding = 10
        self.copy_label_height = 38  # Высота зоны под подпись количества
        self.card_tile_margin = 8  # Отступ плитки вокруг карты
        self.section_gap = 30  # Промежуток между секциями
        self.card_tile_color = (18, 20, 28)
        self.card_tile_border_color = (55, 60, 75)
        
        # Цвета для оверлеев (ленты)
        self.etc_ribbon_color = (34, 139, 34)  # Зеленый для E.T.C.
        self.zilliax_ribbon_color = (138, 43, 226)  # Фиолетовый для Zilliax
        
        # Золотой цвет для акцентов
        self.gold_color = (255, 215, 0)
        self.gold_dark = (184, 134, 11)

        # Актуальные ID владельцев сайдбордов из базы, если доступны
        self.ETC_ID = card_db.ETC_BAND_MANAGER_DBFID or self.ETC_ID_FALLBACK
        self.ZILLIAX_ID = card_db.ZILLIAX_DELUXE_3000_DBFID or self.ZILLIAX_ID_FALLBACK
        
        # Стоимость пыли по редкости
        self.dust_costs = {
            'COMMON': 40,
            'RARE': 100,
            'EPIC': 400,
            'LEGENDARY': 1600
        }
        
        # Загружаем шрифты
        self._load_fonts()
        # Загружаем ассеты (логотип и арты классов)
        self._load_assets()

        # Соответствие классов и ассетов (иконки для шапки)
        self.class_art_files = {
            "DRUID": "heroes_druid_icon-optimized.png",
            "HUNTER": "heroes_hunter_icon-optimized.png",
            "MAGE": "heroes_mage_icon-optimized.png",
            "PALADIN": "heroes_paladin_icon-optimized.png",
            "PRIEST": "heroes_priest_icon-optimized.png",
            "ROGUE": "heroes_rogue_icon-optimized.png",
            "SHAMAN": "heroes_shaman_icon-optimized.png",
            "WARLOCK": "heroes_warlock_icon-optimized.png",
            "WARRIOR": "heroes_warrior_icon-optimized.png",
            "DEMONHUNTER": "icon_heroes_dh-optimized.png",
            "DEATHKNIGHT": "heroes_dk_icon-optimized.png",
            "NEUTRAL": "class_custom.png",
        }
        # Соответствие классов и ассетов (арты для фона)
        self.class_background_files = {
            "DRUID": "class_druid.png",
            "HUNTER": "class_hunter.png",
            "MAGE": "class_mage.png",
            "PALADIN": "class_paladin.png",
            "PRIEST": "class_priest.png",
            "ROGUE": "class_rogue.png",
            "SHAMAN": "class_shaman.png",
            "WARLOCK": "class_warlock.png",
            "WARRIOR": "class_custom.png",
            "DEMONHUNTER": "class_demonhunter.png",
            "DEATHKNIGHT": "class_deathknight.png",
            "NEUTRAL": "class_custom.png",
        }
    
    def _load_fonts(self):
        """Загружает доступные шрифты для использования."""
        self.fonts = {}
        font_paths = [
            ("arial.ttf", "arial"),
            ("segoeui.ttf", "segoe"),
            ("DejaVuSans.ttf", "dejavu"),
            ("arialbd.ttf", "arial_bold"),
        ]
        
        for font_path, key in font_paths:
            try:
                self.fonts[key] = ImageFont.truetype(font_path, 40)
            except:
                pass
        
        # Fallback на default
        if not self.fonts:
            self.fonts['default'] = ImageFont.load_default()

    def _load_assets(self) -> None:
        """Загружает логотип и подготавливает пути к артам классов."""
        self.assets_path = self.base_path / "assets"
        self.class_assets_path = self.assets_path / "classs-logo"
        self.class_background_assets_path = self.assets_path / "class"
        self.logo_path = self.assets_path / "logo" / "logo_manacost_2019_02.png"

        self.logo_image = None
        if self.logo_path.exists():
            try:
                self.logo_image = Image.open(self.logo_path).convert("RGBA")
            except Exception as e:
                print(f"Ошибка при загрузке логотипа {self.logo_path}: {e}")

    def _get_logo_for_footer(self) -> Optional[Image.Image]:
        """Возвращает масштабированный логотип для футера."""
        if not self.logo_image:
            return None
        target_height = 26
        ratio = target_height / self.logo_image.height
        target_width = max(1, int(self.logo_image.width * ratio))
        return self.logo_image.resize((target_width, target_height), Image.Resampling.LANCZOS)

    def _fade_image(self, img: Image.Image, alpha: int) -> Image.Image:
        """Уменьшает непрозрачность изображения."""
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        r, g, b, a = img.split()
        a = a.point(lambda px: min(255, int(px * alpha / 255)))
        return Image.merge("RGBA", (r, g, b, a))

    def _fit_image_to_box(self, img: Image.Image, box_w: int, box_h: int) -> Image.Image:
        """Масштабирует изображение по размеру бокса, сохраняя пропорции."""
        if box_w <= 0 or box_h <= 0:
            return img
        ratio = min(box_w / img.width, box_h / img.height)
        target_w = max(1, int(img.width * ratio))
        target_h = max(1, int(img.height * ratio))
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    def _get_deck_class_key(self, hero_dbf_id: Optional[int]) -> str:
        """Определяет ключ класса колоды по dbfId героя."""
        if not hero_dbf_id:
            return "NEUTRAL"
        hero_card = self.card_db.get_card(hero_dbf_id)
        class_key = (hero_card or {}).get("card_class", "")
        return class_key.upper() if class_key else "NEUTRAL"

    def get_class_art_icon(self, hero_dbf_id: Optional[int], max_height: int) -> Optional[Image.Image]:
        """Загружает и масштабирует арт класса до нужной высоты (иконка)."""
        class_key = self._get_deck_class_key(hero_dbf_id)
        filename = self.class_art_files.get(class_key, "class_custom.png")
        class_art_path = self.class_assets_path / filename
        if not class_art_path.exists():
            class_art_path = self.class_assets_path / "class_custom.png"
            if not class_art_path.exists():
                return None
        try:
            img = Image.open(class_art_path).convert("RGBA")
        except Exception as e:
            print(f"Ошибка при загрузке арта класса {class_art_path}: {e}")
            return None
        if img.height == 0:
            return None
        if max_height <= 0:
            return None
        ratio = max_height / img.height
        target_width = max(1, int(img.width * ratio))
        return img.resize((target_width, max_height), Image.Resampling.LANCZOS)

    def get_class_background_art(self, hero_dbf_id: Optional[int], max_height: int) -> Optional[Image.Image]:
        """Загружает и масштабирует арт класса для фона."""
        class_key = self._get_deck_class_key(hero_dbf_id)
        filename = self.class_background_files.get(class_key, "class_custom.png")
        class_art_path = self.class_background_assets_path / filename
        if not class_art_path.exists():
            class_art_path = self.class_background_assets_path / "class_custom.png"
            if not class_art_path.exists():
                return None
        try:
            img = Image.open(class_art_path).convert("RGBA")
        except Exception as e:
            print(f"Ошибка при загрузке фонового арта класса {class_art_path}: {e}")
            return None
        if img.height == 0 or max_height <= 0:
            return None
        ratio = max_height / img.height
        target_width = max(1, int(img.width * ratio))
        return img.resize((target_width, max_height), Image.Resampling.LANCZOS)

    def _resolve_image_path(self, filename: str) -> Optional[Path]:
        """Находит путь к изображению карты с учетом фолбеков."""
        if not filename:
            return None
        search_dirs = [
            self.images_path,
            self.base_path / "cards",
            self.base_path / "cards_images",
            config.BLIZZARD_IMAGE_CACHE_DIR,
        ]
        stem = Path(filename).stem
        extensions = (".png", ".jpg", ".jpeg", ".webp")
        for base_dir in search_dirs:
            path = base_dir / filename
            if path.exists():
                return path
            for ext in extensions:
                alt_path = base_dir / f"{stem}{ext}"
                if alt_path.exists():
                    return alt_path
            try:
                filename_lower = filename.lower()
                for item in base_dir.iterdir():
                    if item.is_file() and item.name.lower() == filename_lower:
                        return item
            except FileNotFoundError:
                continue
        print(f"⚠ Не найдено изображение карты: {filename} (папки: {', '.join(str(d) for d in search_dirs)})")
        return None

    def _download_blizzard_image(self, url: str, target_path: Path) -> Optional[Path]:
        """Скачивает изображение карты из Blizzard API в кэш."""
        if not url:
            return None
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                return target_path
            import urllib.request
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            if not data:
                return None
            target_path.write_bytes(data)
            return target_path
        except Exception as e:
            print(f"Ошибка загрузки изображения из Blizzard API: {e}")
            return None
    
    def _get_font(self, size: int = 40, bold: bool = False) -> ImageFont.FreeTypeFont:
        """Получает шрифт заданного размера с улучшенной поддержкой."""
        # Пробуем загрузить более качественные шрифты
        font_candidates = []
        
        if bold:
            font_candidates.extend([
                ("arialbd.ttf", "arial_bold"),
                ("arial-black.ttf", "arial_black"),
                ("segoeuib.ttf", "segoe_bold"),
            ])
        
        font_candidates.extend([
            ("arial.ttf", "arial"),
            ("segoeui.ttf", "segoe"),
            ("DejaVuSans.ttf", "dejavu"),
            ("DejaVuSans-Bold.ttf", "dejavu_bold") if bold else None,
        ])
        
        # Убираем None значения
        font_candidates = [f for f in font_candidates if f is not None]
        
        for font_path, _ in font_candidates:
            try:
                return ImageFont.truetype(font_path, size)
            except:
                continue
        
        # Fallback на default
        return ImageFont.load_default()
    
    def create_gradient_background(self, width: int, height: int) -> Image.Image:
        """
        Создает радиальный градиентный фон.
        
        Args:
            width: Ширина изображения
            height: Высота изображения
            
        Returns:
            Изображение с радиальным градиентом
        """
        # Создаем базовое изображение
        img = Image.new('RGB', (width, height), color=(10, 15, 20))
        pixels = img.load()
        
        # Центр градиента
        center_x = width // 2
        center_y = height // 2
        
        # Максимальное расстояние от центра (диагональ)
        max_distance = math.sqrt(center_x**2 + center_y**2)
        
        # Цвета
        center_color = (35, 45, 60)  # Dark Navy Blue
        corner_color = (10, 15, 20)  # Almost Black
        
        # Применяем радиальный градиент
        for y in range(height):
            for x in range(width):
                # Расстояние от центра
                dx = x - center_x
                dy = y - center_y
                distance = math.sqrt(dx**2 + dy**2)
                
                # Нормализуем расстояние (0.0 - 1.0)
                normalized = min(distance / max_distance, 1.0)
                
                # Интерполируем цвет
                r = int(center_color[0] + (corner_color[0] - center_color[0]) * normalized)
                g = int(center_color[1] + (corner_color[1] - center_color[1]) * normalized)
                b = int(center_color[2] + (corner_color[2] - center_color[2]) * normalized)
                
                pixels[x, y] = (r, g, b)
        
        return img
    
    def create_card_shadow(self, card_img: Image.Image) -> Image.Image:
        """
        Создает улучшенную тень для карты с эффектом размытия и градиентом.
        
        Args:
            card_img: Изображение карты
            
        Returns:
            Изображение тени
        """
        # Создаем тень большего размера для более мягкого эффекта
        shadow_size = (card_img.width + 20, card_img.height + 20)
        shadow = Image.new('RGBA', shadow_size, color=(0, 0, 0, 0))
        
        # Создаем градиентную маску для более реалистичной тени
        # Тень более интенсивная в центре и прозрачная по краям
        draw = ImageDraw.Draw(shadow)
        center_x, center_y = shadow_size[0] // 2, shadow_size[1] // 2
        max_radius = max(shadow_size[0], shadow_size[1]) // 2
        
        # Рисуем эллиптическую тень с градиентом
        for y in range(shadow_size[1]):
            for x in range(shadow_size[0]):
                dx = x - center_x
                dy = y - center_y
                distance = math.sqrt(dx**2 + dy**2)
                
                # Нормализуем расстояние
                normalized = min(distance / max_radius, 1.0)
                
                # Градиент: более темный в центре, прозрачный по краям
                alpha = int(220 * (1 - normalized**1.5))
                if alpha > 0:
                    shadow.putpixel((x, y), (0, 0, 0, alpha))
        
        # Применяем размытие для более мягкого эффекта
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=12))
        
        return shadow
    
    def create_placeholder(self, card_name: str, dbf_id: int) -> Image.Image:
        """
        Создает placeholder изображение для отсутствующей карты.
        
        Args:
            card_name: Название карты
            dbf_id: Идентификатор карты
            
        Returns:
            Изображение placeholder
        """
        img = Image.new('RGB', (self.card_width, self.card_height), color=(35, 45, 60))
        draw = ImageDraw.Draw(img)
        
        font = self._get_font(20)
        small_font = self._get_font(14)
        
        # Рисуем текст на placeholder
        text = card_name[:20] if len(card_name) > 20 else card_name
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Центрируем текст
        x = (self.card_width - text_width) // 2
        y = (self.card_height - text_height) // 2 - 20
        
        draw.text((x, y), text, fill='white', font=font)
        draw.text((10, self.card_height - 30), f"ID: {dbf_id}", fill='gray', font=small_font)
        
        return img
    
    def get_card_image(self, dbf_id: int, card_name: str) -> Image.Image:
        """
        Умная загрузка изображения карты с fallback на альтернативные версии.
        
        Args:
            dbf_id: Идентификатор карты (dbfId)
            card_name: Английское название карты
            
        Returns:
            Готовое изображение карты (с правильным размером) или placeholder.
        """
        # 1. Пробуем загрузить изображение по текущему dbfId
        filename = self.card_db.get_card_filename(dbf_id)
        if filename:
            image_path = self._resolve_image_path(filename)
            if image_path and image_path.exists():
                try:
                    img = Image.open(image_path).convert("RGBA")
                    img = img.resize((self.card_width, self.card_height), Image.Resampling.LANCZOS)
                    return img
                except Exception as e:
                    print(f"Ошибка при загрузке изображения {image_path}: {e}")
            # Попытка скачать из Blizzard API в кэш
            try:
                card = self.card_db.get_card(dbf_id)
                image_url = (card or {}).get("image")
                if image_url:
                    cache_path = config.BLIZZARD_IMAGE_CACHE_DIR / filename
                    downloaded = self._download_blizzard_image(image_url, cache_path)
                    if downloaded and downloaded.exists():
                        img = Image.open(downloaded).convert("RGBA")
                        img = img.resize((self.card_width, self.card_height), Image.Resampling.LANCZOS)
                        return img
            except Exception as e:
                print(f"Ошибка при загрузке изображения по ссылке: {e}")
        
        # 2. Fallback: пробуем все альтернативные dbfId с таким же именем карты
        alternatives = card_name_to_ids.get(card_name, [])
        for alt_dbf in alternatives:
            if alt_dbf == dbf_id:
                continue
            alt_filename = self.card_db.get_card_filename(alt_dbf)
            if not alt_filename:
                continue
            alt_path = self._resolve_image_path(alt_filename)
            if alt_path and alt_path.exists():
                try:
                    img = Image.open(alt_path).convert("RGBA")
                    img = img.resize((self.card_width, self.card_height), Image.Resampling.LANCZOS)
                    return img
                except Exception as e:
                    print(f"Ошибка при загрузке fallback-изображения {alt_path}: {e}")
            try:
                card = self.card_db.get_card(alt_dbf)
                image_url = (card or {}).get("image")
                if image_url:
                    cache_path = config.BLIZZARD_IMAGE_CACHE_DIR / alt_filename
                    downloaded = self._download_blizzard_image(image_url, cache_path)
                    if downloaded and downloaded.exists():
                        img = Image.open(downloaded).convert("RGBA")
                        img = img.resize((self.card_width, self.card_height), Image.Resampling.LANCZOS)
                        return img
            except Exception as e:
                print(f"Ошибка при загрузке fallback-изображения по ссылке: {e}")
        
        # 3. Если ничего не нашли, возвращаем placeholder
        return self.create_placeholder(card_name, dbf_id)
    
    def add_ribbon_overlay(self, card_img: Image.Image, ribbon_color: Tuple[int, int, int]) -> Image.Image:
        """
        Добавляет треугольную ленту в правом верхнем углу карты.
        
        Args:
            card_img: Изображение карты
            ribbon_color: Цвет ленты (RGB)
            
        Returns:
            Изображение карты с лентой
        """
        result = card_img.copy()
        draw = ImageDraw.Draw(result)
        
        # Размеры треугольника (ленты)
        ribbon_size = 40
        ribbon_x = self.card_width - ribbon_size
        ribbon_y = 0
        
        # Рисуем треугольник (ленту) в правом верхнем углу
        triangle_points = [
            (ribbon_x, ribbon_y),  # Верхний правый угол
            (self.card_width, ribbon_y + ribbon_size),  # Нижний правый угол
            (ribbon_x, ribbon_y + ribbon_size),  # Нижний левый угол треугольника
        ]
        
        draw.polygon(triangle_points, fill=ribbon_color)
        
        # Добавляем темную обводку
        draw.line([(ribbon_x, ribbon_y), (self.card_width, ribbon_y + ribbon_size)], fill=(0, 0, 0), width=2)
        draw.line([(self.card_width, ribbon_y + ribbon_size), (ribbon_x, ribbon_y + ribbon_size)], fill=(0, 0, 0), width=2)
        
        return result
    
    def draw_card_tile(self, canvas: ImageDraw.Draw, x: int, y: int) -> None:
        """
        Рисует плитку под картой, чтобы карта не терялась на фоне.
        
        Args:
            canvas: ImageDraw объект
            x: Координата X верхнего левого угла карты
            y: Координата Y верхнего левого угла карты
        """
        margin = self.card_tile_margin
        rect = [
            x - margin,
            y - margin,
            x + self.card_width + margin,
            y + self.card_height + margin + self.copy_label_height
        ]
        try:
            canvas.rounded_rectangle(
                rect,
                radius=16,
                fill=self.card_tile_color,
                outline=self.card_tile_border_color,
                width=2
            )
        except AttributeError:
            canvas.rectangle(rect, fill=self.card_tile_color, outline=self.card_tile_border_color, width=2)
    
    def draw_copy_label(self, canvas: ImageDraw.Draw, x: int, y: int, count: int) -> None:
        """
        Рисует подпись количества копий под картой (формат 'x2') в стиле предоставленного примера.
        
        Args:
            canvas: ImageDraw объект
            x: Координата X верхнего левого угла карты
            y: Координата Y верхнего левого угла карты
            count: Количество копий карты
        """
        label_text = f"x{count}"
        label_width = self.card_width - 20
        label_height = self.copy_label_height - 6
        label_x = x + (self.card_width - label_width) // 2
        label_y = y + self.card_height + (self.copy_label_height - label_height) // 2
        
        rect = [
            label_x,
            label_y,
            label_x + label_width,
            label_y + label_height
        ]
        try:
            canvas.rounded_rectangle(
                rect,
                radius=10,
                fill=(22, 26, 36),
                outline=(90, 100, 130),
                width=2
            )
        except AttributeError:
            canvas.rectangle(rect, fill=(22, 26, 36), outline=(90, 100, 130), width=2)
        
        font = self._get_font(20, bold=True)
        bbox = canvas.textbbox((0, 0), label_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        text_x = label_x + (label_width - text_width) // 2
        text_y = label_y + (label_height - text_height) // 2
        
        canvas.text((text_x, text_y), label_text, fill="white", font=font)
    
    def draw_gold_separator(self, img: ImageDraw.Draw, y: int, width: int, title: str):
        """
        Рисует золотую разделительную линию с заголовком.
        
        Args:
            img: ImageDraw объект
            y: Y координата линии
            width: Ширина линии
            title: Текст заголовка
        """
        # Рисуем золотую линию
        line_thickness = 3
        img.rectangle(
            [0, y, width, y + line_thickness],
            fill=self.gold_color
        )
        
        # Рисуем заголовок слева от линии
        font = self._get_font(24, bold=True)
        bbox = img.textbbox((0, 0), title, font=font)
        text_height = bbox[3] - bbox[1]
        
        text_y = y - text_height - 5
        img.text((10, text_y), title, fill=self.gold_color, font=font, stroke_width=1, stroke_fill=(0, 0, 0))
    
    def calculate_dust_cost(self, cards: List[Tuple[int, int]]) -> int:
        """
        Рассчитывает стоимость пыли для списка карт.
        
        Args:
            cards: Список кортежей (dbf_id, count)
            
        Returns:
            Общая стоимость пыли
        """
        total_dust = 0
        
        for dbf_id, count in cards:
            card = self.card_db.get_card(dbf_id)
            if not card:
                continue
            
            # Пропускаем неколлекционные карты (Core set и т.д.)
            if not card.get('collectible', True):
                continue
            
            rarity = card.get('rarity', '').upper()
            dust_per_card = self.dust_costs.get(rarity, 0)
            total_dust += dust_per_card * count
        
        return total_dust
    
    def calculate_deck_stats(self, cards: List[Tuple[int, int]]) -> Dict:
        """
        Рассчитывает статистику колоды.
        
        Args:
            cards: Список кортежей (dbf_id, count)
            
        Returns:
            Словарь со статистикой: avg_cost, total_cards, legendary_count, epic_count
        """
        total_cost = 0
        total_cards = 0
        legendary_count = 0
        epic_count = 0
        
        for dbf_id, count in cards:
            card = self.card_db.get_card(dbf_id)
            if not card:
                continue
            
            total_cards += count
            
            # Стоимость маны
            cost = card.get('cost', 0)
            total_cost += cost * count
            
            # Редкость
            rarity = card.get('rarity', '').upper()
            if rarity == 'LEGENDARY':
                legendary_count += count
            elif rarity == 'EPIC':
                epic_count += count
        
        avg_cost = total_cost / total_cards if total_cards > 0 else 0
        
        return {
            'avg_cost': round(avg_cost, 1),
            'total_cards': total_cards,
            'legendary_count': legendary_count,
            'epic_count': epic_count
        }
    
    def create_legendary_glow(self, card_img: Image.Image) -> Image.Image:
        """
        Создает золотое свечение вокруг легендарной карты.
        
        Args:
            card_img: Изображение карты
            
        Returns:
            Изображение с эффектом свечения
        """
        # Создаем изображение большего размера для свечения
        glow_size = (card_img.width + 16, card_img.height + 16)
        glow = Image.new('RGBA', glow_size, color=(0, 0, 0, 0))
        
        # Создаем золотое свечение
        draw = ImageDraw.Draw(glow)
        center_x, center_y = glow_size[0] // 2, glow_size[1] // 2
        
        # Рисуем несколько слоев свечения для более реалистичного эффекта
        for radius in range(8, 0, -2):
            alpha = int(80 * (1 - radius / 8))
            if alpha > 0:
                # Эллиптическое свечение
                bbox = [
                    center_x - card_img.width // 2 - radius,
                    center_y - card_img.height // 2 - radius,
                    center_x + card_img.width // 2 + radius,
                    center_y + card_img.height // 2 + radius
                ]
                # Используем золотой цвет с прозрачностью
                color = (*self.gold_color, alpha)
                # Рисуем эллипс (приближенно через прямоугольник с закругленными углами)
                try:
                    draw.ellipse(bbox, fill=color)
                except:
                    # Fallback для старых версий PIL
                    draw.ellipse(bbox, fill=self.gold_color)
        
        # Применяем размытие
        glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
        
        # Объединяем свечение с картой
        result = Image.new('RGBA', glow_size, color=(0, 0, 0, 0))
        result.paste(glow, (0, 0), glow)
        
        # Вставляем карту в центр
        card_x = (glow_size[0] - card_img.width) // 2
        card_y = (glow_size[1] - card_img.height) // 2
        
        if card_img.mode != 'RGBA':
            card_img = card_img.convert('RGBA')
        result.paste(card_img, (card_x, card_y), card_img)
        
        return result
    
    def get_format_name(self, format_id: Optional[int]) -> str:
        """
        Преобразует ID формата в название.
        
        Args:
            format_id: ID формата из deck.format (1=Wild, 2=Standard)
            
        Returns:
            Название формата на русском
        """
        if format_id == 2:
            return "Стандартный"
        elif format_id == 1:
            return "Вольный"
        else:
            return "Стандартный"
    
    def render_card_section(self, cards: List[Tuple[int, int]], cards_per_row: int, 
                           ribbon_color: Optional[Tuple[int, int, int]] = None) -> Tuple[List[Dict], int]:
        """
        Подготавливает данные секции карт с учетом группировки и эффектов.
        
        Args:
            cards: Список кортежей (dbf_id, count)
            cards_per_row: Количество карт в ряду
            ribbon_color: Цвет ленты для оверлея (если нужен)
            
        Returns:
            Кортеж (список словарей с данными по картам, количество рядов)
        """
        if not cards:
            return [], 0
        
        # Группируем карты (dbfId -> суммарное количество)
        grouped: Dict[int, int] = {}
        for dbf_id, count in cards:
            if count <= 0:
                continue
            grouped[dbf_id] = grouped.get(dbf_id, 0) + count
        
        if not grouped:
            return [], 0
        
        # Подготавливаем список уникальных карт
        grouped_list = []
        for dbf_id, total_count in grouped.items():
            card = self.card_db.get_card(dbf_id)
            grouped_list.append({
                "dbf_id": dbf_id,
                "card": card,
                "count": total_count
            })
        
        # Сортируем по стоимости маны
        def get_sort_key(item: Dict) -> int:
            card = item["card"]
            if card and "cost" in card:
                return card["cost"]
            return 999
        
        grouped_list.sort(key=get_sort_key)
        
        # Подготавливаем изображения для каждой уникальной карты
        card_items: List[Dict] = []
        for item in grouped_list:
            dbf_id = item["dbf_id"]
            card = item["card"]
            count = item["count"]
            card_name = card["name"] if card else f"Card {dbf_id}"
            
            # Умная загрузка изображения
            card_img = self.get_card_image(dbf_id, card_name)
            
            # Добавляем ленту, если нужно
            if ribbon_color:
                card_img = self.add_ribbon_overlay(card_img, ribbon_color)
            
            card_items.append({
                "image": card_img,
                "count": count,
                "dbf_id": dbf_id
            })
        
        num_unique = len(card_items)
        num_rows = (num_unique + cards_per_row - 1) // cards_per_row if cards_per_row > 0 else 1
        
        return card_items, num_rows
    
    def generate_deck_image(self, deck_cards: List[Tuple[int, int]], 
                           sideboards: Optional[Dict[int, List[Tuple[int, int]]]] = None,
                           deck_format: Optional[int] = None,
                           hero_dbf_id: Optional[int] = None) -> Tuple[BytesIO, Dict]:
        """
        Генерирует премиум изображение колоды с поддержкой сайдбордов.
        Безопасное извлечение данных с использованием констант ETC_ID и ZILLIAX_ID.
        
        Args:
            deck_cards: Список кортежей (dbf_id, count) - основная колода
            sideboards: Словарь {owner_dbf_id: [(dbf_id, count), ...]} - сайдборды
            deck_format: ID формата колоды (1=Wild, 2=Standard)
            hero_dbf_id: dbfId героя колоды (для арта класса)
            
        Returns:
            Кортеж (BytesIO объект с изображением, словарь с метаданными)
        """
        # ========== STEP 1: Safe Data Initialization ==========
        
        # Безопасная обработка входных данных
        deck_cards = deck_cards or []
        
        # Force empty dict if None
        sb = sideboards if sideboards is not None else {}
        print(f"DEBUG: Checking Sideboards in dict: {list(sb.keys())}")
        
        # Проверяем, что есть хотя бы одна карта
        total_cards = len(deck_cards) + sum(len(cards) if cards else 0 for cards in sb.values())
        if total_cards == 0:
            raise ValueError("Колода пуста!")
        
        # Определяем формат колоды
        format_name = self.get_format_name(deck_format)
        
        # Собираем все карты для расчета пыли
        all_cards_for_dust = list(deck_cards)
        for sideboard_cards in sb.values():
            if sideboard_cards:
                all_cards_for_dust.extend(sideboard_cards)
        
        # Рассчитываем стоимость пыли
        dust_cost = self.calculate_dust_cost(all_cards_for_dust)
        deck_stats = self.calculate_deck_stats(deck_cards)
        
        
        # ========== STEP 2: Extract E.T.C. Sideboard Cards ==========
        
        has_etc_data = bool(sb and self.ETC_ID in sb)
        has_zilliax_data = bool(sb and self.ZILLIAX_ID in sb)
        
        etc_cards: List[Dict] = []
        etc_owner_entry: Optional[Tuple[int, int]] = None
        if has_etc_data:
            print(f"DEBUG: Found ETC in sideboards! cards={len(sb[self.ETC_ID])}")
            for (card_id, count) in sb[self.ETC_ID]:
                card = self.card_db.get_card(card_id)
                card_name = card.get('name', f'Card {card_id}') if card else f'Card {card_id}'
                card_img = self.get_card_image(card_id, card_name)
                card_img = self.add_ribbon_overlay(card_img, self.etc_ribbon_color)
                etc_cards.append({
                    'image': card_img,
                    'count': count,
                    'id': card_id
                })
        else:
            print("DEBUG: ETC sideboard not found.")
        
        zilliax_cards: List[Dict] = []
        zilliax_owner_entry: Optional[Tuple[int, int]] = None
        if has_zilliax_data:
            print(f"DEBUG: Found Zilliax in sideboards! cards={len(sb[self.ZILLIAX_ID])}")
            for (card_id, count) in sb[self.ZILLIAX_ID]:
                card = self.card_db.get_card(card_id)
                card_name = card.get('name', f'Card {card_id}') if card else f'Card {card_id}'
                card_img = self.get_card_image(card_id, card_name)
                card_img = self.add_ribbon_overlay(card_img, self.zilliax_ribbon_color)
                zilliax_cards.append({
                    'image': card_img,
                    'count': count,
                    'id': card_id
                })
        else:
            print("DEBUG: Zilliax sideboard not found.")
        
        # ========== STEP 4: Extract Main Deck Cards (Exclude Sideboard Cards) ==========
        
        # Исключаем карты из сайдбордов, чтобы не было дубликатов
        main_deck_list = []
        for dbf_id, count in deck_cards:
            if dbf_id == self.ETC_ID:
                etc_owner_entry = (dbf_id, count)
                if not has_etc_data:
                    main_deck_list.append((dbf_id, count))
                continue
            if dbf_id == self.ZILLIAX_ID:
                zilliax_owner_entry = (dbf_id, count)
                if not has_zilliax_data:
                    main_deck_list.append((dbf_id, count))
                continue
            main_deck_list.append((dbf_id, count))
        
        # Сортируем основную колоду по стоимости маны
        def get_card_cost(dbf_id):
            card = self.card_db.get_card(dbf_id)
            return card.get('cost', 999) if card else 999
        
        main_deck_list.sort(key=lambda x: get_card_cost(x[0]))
        
        # Определяем количество карт в основной колоде (для Renathal логики)
        total_main_cards = sum(count for _, count in main_deck_list)
        main_cards_per_row = 8 if total_main_cards > 30 else 7
        
        # Рендерим основную колоду (без лент)
        main_cards_data, main_rows = self.render_card_section(main_deck_list, main_cards_per_row, None)
        
        print(f"DEBUG: Main deck: {len(main_deck_list)} unique cards, {total_main_cards} total cards")
        
        # Переносим владельцев в их секции
        def build_owner_card(entry: Optional[Tuple[int, int]], ribbon_color: Tuple[int, int, int]) -> Optional[Dict]:
            if not entry:
                return None
            owner_id, owner_count = entry
            card = self.card_db.get_card(owner_id)
            card_name = card.get('name', f'Card {owner_id}') if card else f'Card {owner_id}'
            card_img = self.get_card_image(owner_id, card_name)
            card_img = self.add_ribbon_overlay(card_img, ribbon_color)
            return {
                'image': card_img,
                'count': owner_count,
                'id': owner_id
            }
        
        owner_card = build_owner_card(etc_owner_entry, self.etc_ribbon_color)
        if owner_card and has_etc_data:
            etc_cards.insert(0, owner_card)
        
        owner_card = build_owner_card(zilliax_owner_entry, self.zilliax_ribbon_color)
        if owner_card and has_zilliax_data:
            zilliax_cards.insert(0, owner_card)
        
        print(f"DEBUG: ETC block size={len(etc_cards)}, Zilliax block size={len(zilliax_cards)}")
        
        # ========== STEP 5: Calculate Canvas Dimensions ==========
        
        HEADER_HEIGHT = 82
        FOOTER_HEIGHT = 70
        SEPARATOR_HEIGHT = 35  # Высота разделителя с заголовком
        
        slot_width = self.card_width + self.card_tile_margin * 2
        slot_height = self.card_height + self.copy_label_height + self.card_tile_margin * 2
        
        def section_height(rows: int) -> int:
            if rows <= 0:
                return 0
            return self.padding * (rows + 1) + rows * slot_height
        
        def section_width(cols: int) -> int:
            if cols <= 0:
                return 0
            return self.padding * (cols + 1) + cols * slot_width
        
        main_cols = min(main_cards_per_row, len(main_cards_data)) if main_cards_data else 0
        etc_cards_per_row = len(etc_cards) if etc_cards else 0
        if etc_cards_per_row > 5:
            etc_cards_per_row = 5
        zilliax_cards_per_row = len(zilliax_cards) if zilliax_cards else 0
        if zilliax_cards_per_row > self.cards_per_row:
            zilliax_cards_per_row = self.cards_per_row
        
        etc_rows = math.ceil(len(etc_cards) / etc_cards_per_row) if etc_cards_per_row else 0
        zilliax_rows = math.ceil(len(zilliax_cards) / zilliax_cards_per_row) if zilliax_cards_per_row else 0
        
        main_height = section_height(main_rows)
        etc_height = section_height(etc_rows)
        zilliax_height = section_height(zilliax_rows)
        
        max_width = 0
        if main_cols:
            max_width = max(max_width, section_width(main_cols))
        if etc_cards_per_row:
            max_width = max(max_width, section_width(etc_cards_per_row))
        if zilliax_cards_per_row:
            max_width = max(max_width, section_width(zilliax_cards_per_row))
        
        if max_width == 0:
            raise ValueError("Нет карт для отображения!")
        total_height = HEADER_HEIGHT
        if main_rows:
            total_height += main_height
        if etc_cards:
            if total_height:
                total_height += self.section_gap
            total_height += SEPARATOR_HEIGHT + etc_height
        if zilliax_cards:
            if total_height:
                total_height += self.section_gap
            total_height += SEPARATOR_HEIGHT + zilliax_height
        total_height += FOOTER_HEIGHT
        
        print(f"DEBUG: Canvas size: {max_width}x{total_height}")
        print(f"DEBUG: Main rows: {main_rows}, ETC rows: {etc_rows}, Zilliax rows: {zilliax_rows}")
        
        # ========== STEP 6: Create Premium Background ==========
        
        deck_image = self.create_gradient_background(max_width, total_height)
        draw = ImageDraw.Draw(deck_image)
        
        def draw_cards_section(cards_data: List[Dict], cards_per_row: int, rows: int, y_offset: int, 
                               check_legendary: bool = False) -> int:
            if not cards_data or rows == 0:
                return y_offset
            per_row = max(1, cards_per_row)
            for idx, card_data in enumerate(cards_data):
                card_img = card_data['image']
                card_count = card_data['count']
                dbf_id = card_data.get('dbf_id', card_data.get('id'))
                
                # Проверяем легендарность для эффекта свечения
                is_legendary = False
                if check_legendary and dbf_id:
                    card = self.card_db.get_card(dbf_id)
                    if card and card.get('rarity', '').upper() == 'LEGENDARY':
                        is_legendary = True
                        card_img = self.create_legendary_glow(card_img)
                
                row = idx // per_row
                col = idx % per_row
                base_x = self.padding + col * (slot_width + self.padding)
                base_y = y_offset + self.padding + row * (slot_height + self.padding)
                
                # Корректируем позицию если есть свечение
                glow_offset = 8 if is_legendary else 0
                card_x = base_x + self.card_tile_margin - glow_offset
                card_y = base_y + self.card_tile_margin - glow_offset
                
                self.draw_card_tile(draw, card_x + glow_offset, card_y + glow_offset)
                shadow = self.create_card_shadow(card_img)
                deck_image.paste(shadow, (card_x + 4, card_y + 4), shadow)
                deck_image.paste(card_img, (card_x, card_y), card_img if card_img.mode == 'RGBA' else None)
                
                if card_count > 1:
                    self.draw_copy_label(draw, card_x + glow_offset, card_y + glow_offset, card_count)
            return y_offset + section_height(rows)
        
        # ========== STEP 7: Draw Header ==========
        
        header_rect = [0, 0, max_width, HEADER_HEIGHT]
        draw.rectangle(header_rect, fill=(16, 20, 30))
        draw.rectangle([0, HEADER_HEIGHT - 3, max_width, HEADER_HEIGHT], fill=self.gold_color)
        
        header_font = self._get_font(28, bold=True)
        meta_font = self._get_font(20, bold=False)
        small_font = self._get_font(18, bold=False)
        
        class_icon = self.get_class_art_icon(hero_dbf_id, 48)
        x_cursor = 18
        if class_icon:
            deck_image.paste(class_icon, (x_cursor, 16), class_icon)
            x_cursor += class_icon.width + 12
        
        format_text = f"Режим: {format_name}"
        draw.text((x_cursor, 12), format_text, fill="white", font=header_font)
        
        dust_text = f"Пыль: {dust_cost:,}".replace(",", " ")
        draw.text((x_cursor, 44), dust_text, fill=self.gold_color, font=meta_font)
        
        right_stats = [
            f"Карт: {deck_stats['total_cards']}",
            f"Средняя мана: {deck_stats['avg_cost']}",
        ]
        right_x = max_width - 14
        right_y = 16
        for line in right_stats:
            bbox = draw.textbbox((0, 0), line, font=small_font)
            text_w = bbox[2] - bbox[0]
            draw.text((right_x - text_w, right_y), line, fill=(210, 220, 235), font=small_font)
            right_y += 18
        
        # ========== STEP 8: Draw Main Deck Section ==========
        
        y_offset = HEADER_HEIGHT
        main_section_start_y = 0
        if main_rows:
            y_offset = draw_cards_section(main_cards_data, main_cards_per_row, main_rows, y_offset, check_legendary=True)

        # Водяной знак класса на фоне
        class_art = self.get_class_background_art(hero_dbf_id, int(total_height * 0.45))
        if class_art:
            class_art = self._fade_image(class_art, 60)
            watermark_x = max_width - class_art.width - 20
            watermark_y = HEADER_HEIGHT + 10
            deck_image.paste(class_art, (watermark_x, watermark_y), class_art)
        
        # ========== STEP 9: Draw E.T.C. Sideboard Section ==========
        
        if etc_cards:
            if y_offset > 0:
                y_offset += self.section_gap
            self.draw_gold_separator(draw, y_offset, max_width, "🎸 E.T.C. Band")
            y_offset += SEPARATOR_HEIGHT
            y_offset = draw_cards_section(etc_cards, etc_cards_per_row, etc_rows, y_offset)
        
        # ========== STEP 10: Draw Zilliax Sideboard Section ==========
        
        if zilliax_cards:
            if y_offset > 0:
                y_offset += self.section_gap
            self.draw_gold_separator(draw, y_offset, max_width, "🤖 Zilliax Modules")
            y_offset += SEPARATOR_HEIGHT
            y_offset = draw_cards_section(zilliax_cards, zilliax_cards_per_row, zilliax_rows, y_offset)
        
        # ========== STEP 11: Draw Footer Stats ==========
        footer_y = total_height - FOOTER_HEIGHT
        draw.rectangle([0, footer_y, max_width, total_height], fill=(14, 18, 26))
        draw.rectangle([0, footer_y, max_width, footer_y + 2], fill=self.gold_dark)
        
        footer_font = self._get_font(20, bold=True)
        footer_small = self._get_font(18, bold=False)
        footer_text = "Создано с помощью Manacost"
        draw.text((18, footer_y + 20), footer_text, fill="white", font=footer_font)
        
        stats_text = f"Карт: {deck_stats['total_cards']} • Средняя мана: {deck_stats['avg_cost']}"
        bbox = draw.textbbox((0, 0), stats_text, font=footer_small)
        text_w = bbox[2] - bbox[0]
        draw.text((max_width - text_w - 18, footer_y + 24), stats_text, fill=(180, 195, 215), font=footer_small)
        
        # Сохраняем в BytesIO
        output = BytesIO()
        deck_image.save(output, format='PNG')
        output.seek(0)
        
        # Возвращаем изображение и метаданные
        metadata = {
            'dust_cost': dust_cost,
            'format_name': format_name
        }
        
        return output, metadata
