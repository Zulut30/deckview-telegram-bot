import os
import json
import asyncio
from datetime import datetime, timedelta
from framework.blizzard_api import get_blizzard_api

BGS_CACHE_PATH = "cache/bgs_cards.json"

class BGSManager:
    def __init__(self):
        self.api = get_blizzard_api(locale="ru_RU")
        self.cards = []
        self.last_update = None

    async def get_cards(self):
        if self.cards and self.last_update and (datetime.now() - self.last_update) < timedelta(hours=24):
            return self.cards

        # Try to load from local cache file first
        if os.path.exists(BGS_CACHE_PATH):
            try:
                with open(BGS_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.cards = data["cards"]
                    self.last_update = datetime.fromisoformat(data["updated_at"])
                    if (datetime.now() - self.last_update) < timedelta(hours=24):
                        return self.cards
            except Exception:
                pass

        # Fetch from API
        print("[BGS] Fetching cards from Blizzard API...")
        data = await self.api.get_bgs_cards()
        if "error" in data:
            print(f"[BGS] Error fetching cards: {data['error']}")
            return self.cards

        self.cards = data.get("cards", [])
        self.last_update = datetime.now()

        # Save to cache
        os.makedirs(os.path.dirname(BGS_CACHE_PATH), exist_ok=True)
        with open(BGS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "cards": self.cards,
                "updated_at": self.last_update.isoformat()
            }, f, ensure_ascii=False, indent=2)

        return self.cards

    async def get_grouped_cards(self):
        cards = await self.get_cards()
        heroes = [c for c in cards if c.get("cardTypeId") == 39] # Battlegrounds Hero
        minions = [c for c in cards if c.get("cardTypeId") == 38] # Battlegrounds Minion

        # Sort minions by tier
        minions.sort(key=lambda x: x.get("battlegrounds", {}).get("tier", 1))

        return {
            "heroes": heroes,
            "minions": minions
        }
