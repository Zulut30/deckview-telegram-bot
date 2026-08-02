import os

from db.config import FOLDER
from framework.http_session import get_http_session

# Документация артов: https://hearthstonejson.com/docs/images.html
# Render: https://art.hearthstonejson.com/v1/render/latest/{LOCALE}/512x/{CARD_ID}.png
HSJSON_ART_BASE = "https://art.hearthstonejson.com/v1/render/latest"
HSJSON_ART_LOCALE = "ruRU"


def download_from_hearthstonejson(card_id, slug, locale=None):
    """
    Fallback: скачать арт карты через HearthstoneJSON Art API.
    card_id: строковый id карты (например 'RLK_706') или число dbfId (тогда ищем через HSJSON API).
    slug: имя файла без расширения (например '127410-consumption').
    locale: локаль для render (по умолчанию ruRU).
    """
    if card_id is None:
        return False
    locale = locale or HSJSON_ART_LOCALE
    card_id_str = str(card_id).strip()

    url = f"{HSJSON_ART_BASE}/{locale}/512x/{card_id_str}.png"
    # Если передан dbfId (число), пробуем взять карту из HearthstoneJSON API (190920/latest) для корректного image URL
    if card_id_str.isdigit():
        try:
            from framework.hearthstonejson_api import get_card_by_dbfid
            from deckview.config import HSJSON_CARDS_URL, HSJSON_LOCALE  # root config
            from framework.hearthstonejson_api import configure as hsjson_configure
            hsjson_configure(HSJSON_CARDS_URL, HSJSON_LOCALE)
            deckview_card = get_card_by_dbfid(int(card_id_str))
            if deckview_card and deckview_card.get("image"):
                url = deckview_card["image"]
        except Exception:
            pass

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        print(f"Trying HearthstoneJSON for {slug} (ID: {card_id_str})...")
        response = get_http_session().get(url, headers=headers, timeout=10)
        if response.status_code == 200 and not (response.content[:5] == b"<?xml" or len(response.content) < 100):
            os.makedirs(FOLDER, exist_ok=True)
            with open(f"{FOLDER}{slug}.png", "wb") as photo:
                photo.write(response.content)
            print(f"Successfully downloaded {slug} from HearthstoneJSON")
            return True
        if not response.ok:
            print(f"HearthstoneJSON returned status {response.status_code} for {card_id_str}")
    except Exception as e:
        print(f"Error downloading from HearthstoneJSON for {slug}: {e}")
    return False
