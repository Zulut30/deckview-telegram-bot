"""Local Cyrillic title fonts available to bot users."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PIL import ImageFont


_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FONT_KEY = "auto"

FONT_OPTIONS = OrderedDict(
    [
        (
            "hearthstone",
            {
                "label": "Hearthstone",
                "path": _ROOT / "HEARTHSTONE_CYRILLIC.ttf",
                "variation": None,
            },
        ),
        (
            "belwe",
            {
                "label": "Belwe RUS",
                "path": _ROOT / "assets" / "card_showcase" / "belwe-rus.otf",
                "variation": None,
            },
        ),
        (
            "montserrat",
            {
                "label": "Montserrat",
                "path": _ROOT / "assets" / "fonts" / "Montserrat.ttf",
                "variation": "ExtraBold",
            },
        ),
        (
            "oswald",
            {
                "label": "Oswald",
                "path": _ROOT / "assets" / "fonts" / "Oswald.ttf",
                "variation": "Bold",
            },
        ),
        (
            "roboto_slab",
            {
                "label": "Roboto Slab",
                "path": _ROOT / "assets" / "fonts" / "RobotoSlab.ttf",
                "variation": "ExtraBold",
            },
        ),
        (
            "merriweather",
            {
                "label": "Merriweather",
                "path": _ROOT / "assets" / "fonts" / "Merriweather.ttf",
                "variation": "Black",
            },
        ),
        (
            "lato_black",
            {
                "label": "Lato Black",
                "path": _ROOT / "assets" / "fonts" / "Lato-Black.ttf",
                "variation": None,
            },
        ),
        (
            "noto_serif",
            {
                "label": "Noto Serif",
                "path": _ROOT / "assets" / "fonts" / "NotoSerif.ttf",
                "variation": "Black",
            },
        ),
        (
            "inter",
            {
                "label": "Inter",
                "path": _ROOT / "assets" / "fonts" / "Inter.ttf",
                "variation": "ExtraBold",
            },
        ),
        (
            "open_sans",
            {
                "label": "Open Sans",
                "path": _ROOT / "assets" / "fonts" / "OpenSans.ttf",
                "variation": "ExtraBold",
            },
        ),
        (
            "roboto_condensed",
            {
                "label": "Roboto Condensed",
                "path": _ROOT / "assets" / "fonts" / "RobotoCondensed.ttf",
                "variation": "Bold",
            },
        ),
        (
            "source_sans",
            {
                "label": "Source Sans 3",
                "path": _ROOT / "assets" / "fonts" / "SourceSans3.ttf",
                "variation": "Black",
            },
        ),
        (
            "source_serif",
            {
                "label": "Source Serif 4",
                "path": _ROOT / "assets" / "fonts" / "SourceSerif4.ttf",
                "variation": "Black",
            },
        ),
        (
            "roboto",
            {
                "label": "Roboto",
                "path": _ROOT / "assets" / "fonts" / "Roboto.ttf",
                "variation": "ExtraBold",
            },
        ),
    ]
)


def normalize_font_key(value: str | None) -> str:
    key = str(value or DEFAULT_FONT_KEY).strip().lower()
    return key if key == DEFAULT_FONT_KEY or key in FONT_OPTIONS else DEFAULT_FONT_KEY


def font_label(value: str | None) -> str:
    key = normalize_font_key(value)
    return (
        "Автоматически по стилю"
        if key == DEFAULT_FONT_KEY
        else str(FONT_OPTIONS[key]["label"])
    )


def load_title_font(value: str | None, size: int) -> ImageFont.FreeTypeFont:
    key = normalize_font_key(value)
    option = FONT_OPTIONS["hearthstone" if key == DEFAULT_FONT_KEY else key]
    path = Path(option["path"])
    if not path.is_file():
        option = FONT_OPTIONS["hearthstone"]
        path = Path(option["path"])
    font = ImageFont.truetype(str(path), int(size))
    variation = option.get("variation")
    if variation:
        try:
            font.set_variation_by_name(str(variation))
        except (AttributeError, OSError, ValueError):
            pass
    return font
