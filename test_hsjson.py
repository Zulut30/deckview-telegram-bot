import asyncio
import os
import sys

# Add the project directory to sys.path
sys.path.append('/home/ubuntu/Deckview')

from framework.hearthstonejson_downloader import download_from_hearthstonejson

async def test_fallback():
    # Test with a known Card ID (e.g. SW_032 for Touch of the Shades)
    # For Health Drink, we need its CardID (e.g. VAC_406)
    slug = "107923-health-drink"
    card_id = "VAC_406" # Health Drink

    # Path to check
    path = f"cards/{slug}.png"
    if os.path.exists(path):
        os.remove(path)

    print(f"Testing download for {slug}...")
    success = download_from_hearthstonejson(card_id, slug)

    if success and os.path.exists(path):
        print(f"SUCCESS: Image downloaded to {path}")
        print(f"File size: {os.path.getsize(path)} bytes")
    else:
        print("FAILED: Image not downloaded")

if __name__ == "__main__":
    asyncio.run(test_fallback())
