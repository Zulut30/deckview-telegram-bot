"""
Изображение одной карты на фоне обоев для команды /card.
Арт карты загружается из FOLDER (cards/slug.png) или по URL из HearthstoneJSON Art API.
"""
import os
from io import BytesIO
from typing import Any, Dict, Optional

from PIL import Image

from db.config import FOLDER
from framework.http_session import get_http_session

# Корень проекта (Deckview) для пути к assets
_DECKVIEW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WALLPAPER_PATH = os.path.join(_DECKVIEW_ROOT, "assets", "wallpaper for card.png")

# Максимальная доля ширины/высоты фона, которую занимает арт карты (по центру)
CARD_ART_SCALE = 0.88


def get_card_art_image(card: Dict[str, Any]) -> Optional[Image.Image]:
    """
    Получить PIL.Image арта карты: сначала из FOLDER/slug.png, иначе по URL card["image"].
    """
    slug = card.get("slug")
    if not slug:
        return None
    path = os.path.join(_DECKVIEW_ROOT, FOLDER, f"{slug}.png")
    if os.path.isfile(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    url = card.get("image")
    if url:
        try:
            resp = get_http_session().get(url, timeout=15)
            if resp.ok:
                return Image.open(BytesIO(resp.content)).convert("RGBA")
        except Exception:
            pass
    return None


def build_card_image(
    card_art: Image.Image,
    wallpaper_path: Optional[str] = None,
) -> Image.Image:
    """
    Наложить арт карты по центру обоев. Возвращает RGB изображение.
    """
    path = wallpaper_path or WALLPAPER_PATH
    if not os.path.isfile(path):
        # Fallback: однотонный фон
        bg = Image.new("RGBA", (512, 512), (30, 25, 40, 255))
    else:
        bg = Image.open(path).convert("RGBA")
    bw, bh = bg.size
    max_w = int(bw * CARD_ART_SCALE)
    max_h = int(bh * CARD_ART_SCALE)
    card_art = card_art.copy()
    card_art.thumbnail((max_w, max_h), Image.LANCZOS)
    cw, ch = card_art.size
    x = (bw - cw) // 2
    y = (bh - ch) // 2
    bg.paste(card_art, (x, y), card_art)
    return bg.convert("RGB")
