import os
from PIL import ImageFont

# Путь к шрифту: всегда из корня проекта Deckview (для прода и любого cwd)
_DECKVIEW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(_DECKVIEW_ROOT, "HEARTHSTONE_CYRILLIC.ttf")

# Основной шрифт для текста на изображении
FONT = ImageFont.truetype(FONT_PATH, 128)
