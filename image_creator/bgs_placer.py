import os
from PIL import Image, ImageDraw
from threader import to_thread
from .cards_downloader import download_cards

FOLDER = "cards/"
BG_PATH = "backs/bgs_bg.png"

async def place_bgs_board(hero_data, minions_data):
    """
    Generate a BGS board image.
    hero_data: Card object for the hero
    minions_data: List of 7 card objects for minions (can contain None)
    """
    # Download cards if missing
    to_download = [hero_data] + [m for m in minions_data if m]
    await download_cards(to_download)

    return await _place_bgs_board_sync(hero_data["id"], [m["id"] if m else None for m in minions_data])

@to_thread
def _place_bgs_board_sync(hero_id, minion_ids):
    # Create background (wide for 7 minions + hero)
    # Standard hearthstone card is ~1.35 aspect ratio
    # 7 minions * 300px + some spacing
    width = 2400
    height = 1000

    # Try to load a nice background or create one
    if os.path.exists(BG_PATH):
        image = Image.open(BG_PATH).resize((width, height))
    else:
        # Gradient background
        image = Image.new("RGBA", (width, height), (30, 30, 40, 255))
        draw = ImageDraw.Draw(image)
        for i in range(height):
            color = (30 + i // 20, 30 + i // 30, 40 + i // 15, 255)
            draw.line([(0, i), (width, i)], fill=color)

    # Place Hero (centered on top or left)
    hero_path = f"{FOLDER}{hero_id}.png"
    if os.path.exists(hero_path):
        hero_im = Image.open(hero_path).convert("RGBA")
        hero_im = hero_im.resize((400, 540))
        image.paste(hero_im, (width // 2 - 200, 50), mask=hero_im)

    # Place Minions (centered on bottom)
    minion_width = 300
    minion_height = int(minion_width * 1.354)
    spacing = 20
    total_minion_width = (minion_width * 7) + (spacing * 6)
    start_x = (width - total_minion_width) // 2
    y_pos = 600

    for i, mid in enumerate(minion_ids):
        if not mid:
            continue

        path = f"{FOLDER}{mid}.png"
        if os.path.exists(path):
            try:
                m_im = Image.open(path).convert("RGBA")
                m_im = m_im.resize((minion_width, minion_height))
                x_pos = start_x + (minion_width + spacing) * i
                image.paste(m_im, (x_pos, y_pos), mask=m_im)
            except Exception as e:
                print(f"Error placing BGS minion {mid}: {e}")

    return image
