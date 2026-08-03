import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")

BATTLE_NET_TOKEN = os.getenv("BATTLE_NET_TOKEN")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FOLDER = str(
    Path(os.getenv("DECKVIEW_CARD_CACHE_DIR", PROJECT_ROOT / "cards")).resolve()
) + os.sep
