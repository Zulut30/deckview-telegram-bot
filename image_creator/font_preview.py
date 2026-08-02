"""Typography chooser preview rendered with the exact bundled fonts."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw

from .card_showcase import CANVAS_SIZE, parchment_background, wood_frame_overlay
from .font_catalog import FONT_OPTIONS, load_title_font, normalize_font_key


def _fit_font(font_key: str, text: str, maximum_width: int, start_size: int):
    for size in range(start_size, 25, -2):
        font = load_title_font(font_key, size)
        box = font.getbbox(text, stroke_width=1)
        if box[2] - box[0] <= maximum_width:
            return font
    return load_title_font(font_key, 26)


@lru_cache(maxsize=16)
def build_font_preview(active_font: str | None = None) -> bytes:
    active = normalize_font_key(active_font)
    size = (900, 1660)
    image = parchment_background(size)
    draw = ImageDraw.Draw(image)
    heading = load_title_font("montserrat", 50)
    draw.multiline_text(
        (size[0] // 2, 82),
        "ШРИФТЫ ДЛЯ ЗАГОЛОВКА",
        font=heading,
        fill=(45, 35, 28),
        anchor="mm",
        align="center",
        stroke_width=1,
        stroke_fill=(238, 213, 159),
    )

    margin_x = 70
    top = 145
    bottom = 1585
    column_gap = 24
    cell_w = (size[0] - margin_x * 2 - column_gap) // 2
    rows = (len(FONT_OPTIONS) + 1) // 2
    cell_h = (bottom - top) // rows
    sample = "ЛЕГЕНДА"

    draw.line(
        (size[0] // 2, top, size[0] // 2, bottom),
        fill=(91, 68, 48, 120),
        width=2,
    )
    for row in range(1, rows):
        y = top + row * cell_h
        draw.line(
            (margin_x, y, size[0] - margin_x, y),
            fill=(91, 68, 48, 80),
            width=1,
        )

    for index, (font_key, option) in enumerate(FONT_OPTIONS.items()):
        column = index % 2
        row = index // 2
        x0 = margin_x + column * (cell_w + column_gap)
        y0 = top + row * cell_h
        center_x = x0 + cell_w // 2
        is_active = font_key == active
        if is_active:
            overlay = Image.new("RGBA", size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rounded_rectangle(
                (x0 + 10, y0 + 12, x0 + cell_w - 10, y0 + cell_h - 12),
                radius=18,
                fill=(139, 88, 40, 28),
                outline=(124, 72, 30, 155),
                width=3,
            )
            image = Image.alpha_composite(image, overlay)
            draw = ImageDraw.Draw(image)

        font = _fit_font(font_key, sample, cell_w - 36, 52)
        draw.text(
            (center_x + 2, y0 + 86),
            sample,
            font=font,
            fill=(94, 63, 39, 90),
            anchor="mm",
        )
        draw.text(
            (center_x, y0 + 83),
            sample,
            font=font,
            fill=(39, 31, 26),
            anchor="mm",
            stroke_width=1,
            stroke_fill=(239, 214, 161),
        )
        label_font = load_title_font("montserrat", 19)
        label = f"{'АКТИВЕН · ' if is_active else ''}{option['label']}"
        draw.text(
            (center_x, y0 + 146),
            label,
            font=label_font,
            fill=(73, 58, 47),
            anchor="mm",
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
