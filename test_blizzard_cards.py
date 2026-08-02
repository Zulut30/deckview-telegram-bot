import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the project directory to sys.path
sys.path.append('/home/ubuntu/Deckview')

from framework.blizzard_api import BlizzardAPI

async def check_cards():
    load_dotenv('/home/ubuntu/Deckview/.env')
    token = os.getenv("BATTLE_NET_TOKEN")
    api = BlizzardAPI(token, locale="ru_RU")

    card_ids = [107923, 62892]
    for cid in card_ids:
        print(f"Checking card {cid}...")
        card = await api.get_card_from_id(cid)
        if "error" in card:
            print(f"Error for {cid}: {card['error']}")
        else:
            print(f"ID: {card.get('id')}")
            print(f"Slug: {card.get('slug')}")
            print(f"Image: {card.get('image')}")
            print(f"Name: {card.get('name')}")

if __name__ == "__main__":
    asyncio.run(check_cards())
