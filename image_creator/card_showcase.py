"""Hearthstone card presentation using the official HS-Arena design assets.

Asset source and visual rules:
https://github.com/Zulut30/manacost-arena/blob/main/assets.md
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


_ROOT = Path(__file__).resolve().parent.parent
_ASSET_DIR = _ROOT / "assets" / "card_showcase"
PARCHMENT_PATH = _ASSET_DIR / "arena-parchment.jpg"
WOOD_FRAME_PATH = _ASSET_DIR / "main-page-rail-border.png"
BELWE_FONT_PATH = _ASSET_DIR / "belwe-rus.otf"

# A square media canvas prevents Telegram clients from stretching a long
# caption wider than a portrait photo and filling the resulting side gaps with
# blurred colour bands.  The parchment and wood frame now occupy the complete
# media area in both card modes.
CANVAS_SIZE = (1250, 1250)
_SOURCE_FRAME_SLICE = 13
_DESTINATION_FRAME_WIDTH = 46
_RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


def _tile_texture(texture: Image.Image, size: tuple[int, int]) -> Image.Image:
    texture = texture.convert("RGB")
    tiled = Image.new("RGB", size)
    for y in range(0, size[1], texture.height):
        for x in range(0, size[0], texture.width):
            tiled.paste(texture, (x, y))
    return tiled


def _paste_resized(
    target: Image.Image,
    source: Image.Image,
    source_box: tuple[int, int, int, int],
    destination_box: tuple[int, int, int, int],
) -> None:
    width = destination_box[2] - destination_box[0]
    height = destination_box[3] - destination_box[1]
    piece = source.crop(source_box).resize((width, height), _RESAMPLE)
    target.alpha_composite(piece, (destination_box[0], destination_box[1]))


def wood_frame_overlay(
    size: tuple[int, int],
    destination_slice: int = _DESTINATION_FRAME_WIDTH,
) -> Image.Image:
    """Resize only the border slices, preserving the wood without its dark fill."""
    width, height = (max(1, int(size[0])), max(1, int(size[1])))
    destination_slice = max(12, min(int(destination_slice), min(width, height) // 4))
    revision = _asset_revision(WOOD_FRAME_PATH)
    return _cached_wood_frame_overlay(
        (width, height),
        destination_slice,
        revision,
    ).copy()


def _asset_revision(path: Path) -> tuple[int, int]:
    """Return a cheap cache revision that changes when an asset is replaced."""
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=4)
def _cached_wood_frame_overlay(
    size: tuple[int, int],
    destination_slice: int,
    _revision: tuple[int, int],
) -> Image.Image:
    with Image.open(WOOD_FRAME_PATH) as source:
        frame = source.convert("RGBA")
    source_width, source_height = frame.size
    source_slice = _SOURCE_FRAME_SLICE
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))

    source_boxes = {
        "tl": (0, 0, source_slice, source_slice),
        "tr": (source_width - source_slice, 0, source_width, source_slice),
        "bl": (0, source_height - source_slice, source_slice, source_height),
        "br": (
            source_width - source_slice,
            source_height - source_slice,
            source_width,
            source_height,
        ),
        "top": (source_slice, 0, source_width - source_slice, source_slice),
        "bottom": (
            source_slice,
            source_height - source_slice,
            source_width - source_slice,
            source_height,
        ),
        "left": (0, source_slice, source_slice, source_height - source_slice),
        "right": (
            source_width - source_slice,
            source_slice,
            source_width,
            source_height - source_slice,
        ),
    }
    destination_boxes = {
        "tl": (0, 0, destination_slice, destination_slice),
        "tr": (width - destination_slice, 0, width, destination_slice),
        "bl": (0, height - destination_slice, destination_slice, height),
        "br": (
            width - destination_slice,
            height - destination_slice,
            width,
            height,
        ),
        "top": (destination_slice, 0, width - destination_slice, destination_slice),
        "bottom": (
            destination_slice,
            height - destination_slice,
            width - destination_slice,
            height,
        ),
        "left": (0, destination_slice, destination_slice, height - destination_slice),
        "right": (
            width - destination_slice,
            destination_slice,
            width,
            height - destination_slice,
        ),
    }
    for key in source_boxes:
        _paste_resized(overlay, frame, source_boxes[key], destination_boxes[key])
    return overlay


def parchment_background(size: tuple[int, int]) -> Image.Image:
    """Build the shared warm parchment background used by cards and decks."""
    if not PARCHMENT_PATH.is_file():
        raise FileNotFoundError("Не найден фон пергамента")
    normalized_size = (max(1, int(size[0])), max(1, int(size[1])))
    revision = _asset_revision(PARCHMENT_PATH)
    return _cached_parchment_background(normalized_size, revision).copy()


@lru_cache(maxsize=4)
def _cached_parchment_background(
    size: tuple[int, int],
    _revision: tuple[int, int],
) -> Image.Image:
    with Image.open(PARCHMENT_PATH) as parchment:
        background = _tile_texture(parchment, size).convert("RGBA")
    wash = Image.new("RGBA", size, (247, 232, 191, 34))
    return Image.alpha_composite(background, wash)


def _clear_showcase_layer_cache() -> None:
    """Clear process-local decorative layers after tests or an asset refresh."""
    _cached_parchment_background.cache_clear()
    _cached_wood_frame_overlay.cache_clear()


def _fit_title(
    draw: ImageDraw.ImageDraw,
    name: str,
    maximum_width: int,
) -> tuple[ImageFont.FreeTypeFont, str]:
    clean_name = " ".join(str(name or "Карта").split())
    for font_size in range(60, 29, -2):
        font = ImageFont.truetype(str(BELWE_FONT_PATH), font_size)
        box = draw.textbbox((0, 0), clean_name, font=font, stroke_width=1)
        if box[2] - box[0] <= maximum_width:
            return font, clean_name

    words = clean_name.split()
    if len(words) < 2:
        return ImageFont.truetype(str(BELWE_FONT_PATH), 30), clean_name
    best_lines = None
    best_width = None
    for split_at in range(1, len(words)):
        lines = (" ".join(words[:split_at]), " ".join(words[split_at:]))
        font = ImageFont.truetype(str(BELWE_FONT_PATH), 36)
        widths = [
            draw.textbbox((0, 0), line, font=font, stroke_width=1)[2]
            for line in lines
        ]
        widest = max(widths)
        if widest <= maximum_width and (best_width is None or widest < best_width):
            best_lines = lines
            best_width = widest
    return (
        ImageFont.truetype(str(BELWE_FONT_PATH), 36),
        "\n".join(best_lines or (clean_name,)),
    )


def build_card_showcase(card_image_bytes: bytes, card_name: str) -> bytes:
    """Compose a framed parchment JPEG suitable for Telegram ``sendPhoto``."""
    if not all(path.is_file() for path in (PARCHMENT_PATH, WOOD_FRAME_PATH, BELWE_FONT_PATH)):
        raise FileNotFoundError("Не найдены локальные ассеты оформления карты")

    background = parchment_background(CANVAS_SIZE)

    with Image.open(BytesIO(card_image_bytes)) as source_card:
        card = source_card.convert("RGBA")
    alpha_box = card.getchannel("A").getbbox()
    if alpha_box:
        card = card.crop(alpha_box)
    scale = min(720 / card.width, 990 / card.height)
    card = card.resize(
        (max(1, round(card.width * scale)), max(1, round(card.height * scale))),
        _RESAMPLE,
    )
    card_x = (CANVAS_SIZE[0] - card.width) // 2
    card_y = 32

    shadow_mask = card.getchannel("A").filter(ImageFilter.GaussianBlur(16))
    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", card.size, (42, 24, 12, 135))
    shadow_layer.putalpha(shadow_mask)
    shadow.alpha_composite(shadow_layer, (card_x + 9, card_y + 14))
    background = Image.alpha_composite(background, shadow)
    background.alpha_composite(card, (card_x, card_y))

    draw = ImageDraw.Draw(background)
    divider_y = min(1050, card_y + card.height + 14)
    draw.line(
        (_DESTINATION_FRAME_WIDTH + 70, divider_y, CANVAS_SIZE[0] - _DESTINATION_FRAME_WIDTH - 70, divider_y),
        fill=(95, 55, 29, 150),
        width=3,
    )
    title_font, title = _fit_title(draw, card_name, CANVAS_SIZE[0] - 150)
    title_box = draw.multiline_textbbox(
        (0, 0),
        title,
        font=title_font,
        spacing=4,
        align="center",
        stroke_width=1,
    )
    title_height = title_box[3] - title_box[1]
    title_y = divider_y + max(18, (CANVAS_SIZE[1] - divider_y - title_height) // 2 - 2)
    draw.multiline_text(
        (CANVAS_SIZE[0] // 2 + 2, title_y + 3),
        title,
        font=title_font,
        fill=(76, 43, 23, 110),
        anchor="ma",
        align="center",
        spacing=4,
    )
    draw.multiline_text(
        (CANVAS_SIZE[0] // 2, title_y),
        title,
        font=title_font,
        fill=(48, 37, 28, 255),
        anchor="ma",
        align="center",
        spacing=4,
        stroke_width=1,
        stroke_fill=(239, 207, 140, 210),
    )

    background = Image.alpha_composite(background, wood_frame_overlay(CANVAS_SIZE))
    output = BytesIO()
    background.convert("RGB").save(
        output,
        format="JPEG",
        quality=93,
        optimize=True,
        progressive=True,
    )
    return output.getvalue()


def build_full_art_showcase(art_image_bytes: bytes, card_name: str) -> bytes:
    """Blend the original vertical full art naturally into the parchment."""
    background = parchment_background(CANVAS_SIZE)
    with Image.open(BytesIO(art_image_bytes)) as source:
        art = source.convert("RGB")
    scale = min(690 / art.width, 950 / art.height)
    art = art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        _RESAMPLE,
    )
    art_x = (CANVAS_SIZE[0] - art.width) // 2
    art_y = 42

    # A softly feathered rounded mask makes the illustration feel printed into
    # the parchment instead of looking like a hard rectangular insert.  The
    # stronger lower fade preserves the whole composition while gently merging
    # the floor of the artwork into the title area.
    mask = Image.new("L", art.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (5, 5, art.width - 6, art.height - 6),
        radius=24,
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(3.2))
    bottom_fade = Image.new("L", art.size, 255)
    fade_draw = ImageDraw.Draw(bottom_fade)
    fade_height = min(62, max(28, art.height // 12))
    fade_start = art.height - fade_height
    for y in range(fade_start, art.height):
        progress = (y - fade_start) / max(1, fade_height - 1)
        fade_draw.line(
            (0, y, art.width, y),
            fill=max(0, round(255 * (1.0 - progress**1.7))),
        )
    mask = ImageChops.multiply(mask, bottom_fade)

    art_layer = art.convert("RGBA")
    art_layer.putalpha(mask)
    shadow_mask = mask.filter(ImageFilter.GaussianBlur(16)).point(
        lambda value: round(value * 0.48)
    )
    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", art.size, (42, 24, 12, 0))
    shadow_layer.putalpha(shadow_mask)
    shadow.alpha_composite(shadow_layer, (art_x + 7, art_y + 11))
    background = Image.alpha_composite(background, shadow)
    background.alpha_composite(art_layer, (art_x, art_y))

    draw = ImageDraw.Draw(background)
    divider_y = min(1050, art_y + art.height + 14)
    draw.line(
        (
            _DESTINATION_FRAME_WIDTH + 70,
            divider_y,
            CANVAS_SIZE[0] - _DESTINATION_FRAME_WIDTH - 70,
            divider_y,
        ),
        fill=(95, 55, 29, 150),
        width=3,
    )
    title_font, title = _fit_title(draw, card_name, CANVAS_SIZE[0] - 150)
    title_box = draw.multiline_textbbox(
        (0, 0),
        title,
        font=title_font,
        spacing=4,
        align="center",
        stroke_width=1,
    )
    title_height = title_box[3] - title_box[1]
    title_y = divider_y + max(
        18,
        (CANVAS_SIZE[1] - divider_y - title_height) // 2 - 2,
    )
    draw.multiline_text(
        (CANVAS_SIZE[0] // 2 + 2, title_y + 3),
        title,
        font=title_font,
        fill=(76, 43, 23, 110),
        anchor="ma",
        align="center",
        spacing=4,
    )
    draw.multiline_text(
        (CANVAS_SIZE[0] // 2, title_y),
        title,
        font=title_font,
        fill=(48, 37, 28, 255),
        anchor="ma",
        align="center",
        spacing=4,
        stroke_width=1,
        stroke_fill=(239, 207, 140, 210),
    )
    background = Image.alpha_composite(
        background,
        wood_frame_overlay(CANVAS_SIZE),
    )
    output = BytesIO()
    background.convert("RGB").save(
        output,
        format="JPEG",
        quality=93,
        optimize=True,
        progressive=True,
    )
    return output.getvalue()
