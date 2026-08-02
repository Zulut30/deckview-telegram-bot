import os
import shutil

from db.config import FOLDER
from framework import GRequestsDownloader
from threader import to_thread


@to_thread
def download_cards(cards):
    os.makedirs(FOLDER, exist_ok=True)

    cards_api = GRequestsDownloader()
    cards_api.process_cards(cards)
