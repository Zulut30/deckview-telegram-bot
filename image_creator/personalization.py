"""Validated presentation options beyond background and typography."""

from __future__ import annotations

from typing import Any


DUST_DISPLAY_OPTIONS = {
    "normal": "Обычная",
    "large": "Крупная",
    "hidden": "Скрыта",
}

CLASS_ART_OPTIONS = {
    "class": "Арт класса",
    "logo": "Свой логотип",
}

CARDS_PER_ROW_OPTIONS = (0, 5, 6, 7, 8, 9, 10)
DECK_LAYOUT_LABELS = {
    "normal": "Обычная · до 30 карт",
    "extended": "Расширенная · больше 30 карт",
    "highlander": "Highlander / Reno",
}
MANA_CURVE_OPTIONS = {
    "chart": "Манакривая",
    "hidden": "Скрыта",
    "image": "Своя картинка",
}


def normalize_dust_display(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in DUST_DISPLAY_OPTIONS else "normal"


def normalize_class_art_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in CLASS_ART_OPTIONS else "class"


def normalize_cards_per_row(value: Any, *, allow_inherit: bool = False) -> int:
    """Return 0 for automatic layout, -1 for a managed-chat inheritance."""
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = -1 if allow_inherit else 0
    if allow_inherit and normalized == -1:
        return -1
    return normalized if normalized in CARDS_PER_ROW_OPTIONS else 0


def normalize_mana_curve_mode(value: Any, *, allow_inherit: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_inherit and normalized == "inherit":
        return "inherit"
    return normalized if normalized in MANA_CURVE_OPTIONS else "chart"


def classify_deck_layout(counters, sideboard_slugs=None) -> str:
    """Classify by the main-deck composition; explicit sideboards are ignored."""
    sideboards = set(sideboard_slugs or ())
    main_counts = [
        max(0, int(count or 0))
        for card_id, count in counters.items()
        if card_id not in sideboards
    ]
    total = sum(main_counts)
    if total >= 30 and main_counts and all(count <= 1 for count in main_counts):
        return "highlander"
    return "extended" if total > 30 else "normal"


def resolve_cards_per_row(layout: Any, category: str, displayed_cards: int) -> int:
    settings = layout if isinstance(layout, dict) else {}
    requested = normalize_cards_per_row(settings.get(category))
    if requested:
        return max(1, min(int(displayed_cards or 1), requested))
    # Established automatic composition: six columns for short lists, eight
    # for ordinary 30-card decks, ten for very dense/extended lists.
    card_width = 500 if displayed_cards <= 18 else 375 if displayed_cards <= 32 else 300
    return max(1, min(int(displayed_cards or 1), 3000 // card_width))
