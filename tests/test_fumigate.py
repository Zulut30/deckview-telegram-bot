import asyncio
import sys
import os
from pathlib import Path

# Add the project directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from framework.hearthstonejson_downloader import download_from_hearthstonejson
from framework.wiki_downloader import download_from_wiki

async def test_specific_card():
    slug = "117685-fumigate"
    card_id = "VAC_405" # I suspect VAC_405 is Fumigate based on VAC_406 being Health Drink
    name = "Fumigate"

    print(f"Testing download for {slug}...")

    # Try wiki first
    print("Trying Wiki...")
    if download_from_wiki(slug, name):
        print("SUCCESS from Wiki")
        return

    # Try HSJSON
    # First, let's try some common ID patterns if VAC_405 is wrong
    # We can also try the dbf_id if the downloader supports it, or just a range
    ids_to_try = ["VAC_405", "VAC_404", "VAC_407"]
    for cid in ids_to_try:
        print(f"Trying HSJSON with ID {cid}...")
        if download_from_hearthstonejson(cid, slug):
            print(f"SUCCESS from HSJSON with {cid}")
            return

    print("FAILED all sources")

if __name__ == "__main__":
    asyncio.run(test_specific_card())
