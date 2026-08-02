"""Preview sheet for the built-in custom-background color presets."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw

from .card_showcase import parchment_background, wood_frame_overlay
from .custom_background import GRADIENT_PRESETS, gradient_background
from .font_catalog import load_title_font


def _rounded_tile(tile: Image.Image, radius: int = 22) -> Image.Image:
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, tile.width - 1, tile.height - 1),
        radius=radius,
        fill=255,
    )
    result = tile.convert("RGBA")
    result.putalpha(mask)
    return result


@lru_cache(maxsize=16)
def build_gradient_preview(active_value: str | None = None) -> bytes:
    """Render every preset exactly as it will appear behind a deck."""
    size = (900, 1280)
    image = parchment_background(size)
    draw = ImageDraw.Draw(image)
    heading = load_title_font("montserrat", 45)
    draw.text(
        (size[0] // 2, 74),
        "ФОНЫ И ГРАДИЕНТЫ",
        font=heading,
        fill=(45, 35, 28),
        anchor="mm",
        stroke_width=1,
        stroke_fill=(238, 213, 159),
    )

    margin_x = 68
    top = 125
    gap_x = 24
    gap_y = 18
    columns = 2
    rows = (len(GRADIENT_PRESETS) + columns - 1) // columns
    cell_w = (size[0] - margin_x * 2 - gap_x) // columns
    cell_h = (1100 - gap_y * (rows - 1)) // rows
    label_font = load_title_font("montserrat", 21)
    sample_font = load_title_font("montserrat", 25)

    for index, (_key, (label, value)) in enumerate(GRADIENT_PRESETS.items()):
        column = index % columns
        row = index // columns
        x = margin_x + column * (cell_w + gap_x)
        y = top + row * (cell_h + gap_y)
        active = str(active_value or "").upper() == value.upper()

        tile = gradient_background((cell_w, cell_h), value)
        tile_draw = ImageDraw.Draw(tile)
        tile_draw.rounded_rectangle(
            (15, 16, cell_w - 15, 60),
            radius=14,
            fill=(5, 9, 14, 65),
        )
        tile_draw.text(
            (cell_w // 2, 38),
            "МОЯ КОЛОДА",
            font=sample_font,
            fill=(255, 255, 255, 245),
            anchor="mm",
            stroke_width=2,
            stroke_fill=(8, 12, 18, 180),
        )
        baseline = cell_h - 38
        bar_width = 25
        for bar_index, bar_height in enumerate((34, 58, 82, 52, 72, 42)):
            bx = 24 + bar_index * (bar_width + 9)
            tile_draw.rounded_rectangle(
                (bx, baseline - bar_height, bx + bar_width, baseline),
                radius=5,
                fill=(125, 205, 255, 215),
                outline=(255, 255, 255, 150),
                width=1,
            )
        tile_draw.text(
            (cell_w - 18, cell_h - 19),
            label.split(" ", 1)[-1],
            font=label_font,
            fill=(255, 255, 255, 248),
            anchor="rs",
            stroke_width=2,
            stroke_fill=(8, 12, 18, 205),
        )
        image.alpha_composite(_rounded_tile(tile), (x, y))
        if active:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (x - 4, y - 4, x + cell_w + 4, y + cell_h + 4),
                radius=26,
                outline=(255, 219, 120, 255),
                width=5,
            )

    image = Image.alpha_composite(image, wood_frame_overlay(size))
    output = BytesIO()
    image.convert("RGB").save(
        output,
        format="JPEG",
        quality=92,
        optimize=True,
        progressive=True,
    )
    return output.getvalue()
