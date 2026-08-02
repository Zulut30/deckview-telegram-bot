"""Validated deck-title size presets shared by settings and renderers."""

from __future__ import annotations

from typing import Any


TITLE_SIZE_OPTIONS = {
    "small": {"label": "Маленький", "scale": 0.78},
    "normal": {"label": "Обычный", "scale": 1.0},
    "large": {"label": "Крупный", "scale": 1.35},
    "xlarge": {"label": "Очень крупный", "scale": 1.75},
    "huge": {"label": "Огромный", "scale": 2.1},
}


def normalize_title_size(value: Any, *, allow_inherit: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_inherit and normalized == "inherit":
        return "inherit"
    return normalized if normalized in TITLE_SIZE_OPTIONS else "normal"


def title_size_label(value: Any, *, allow_inherit: bool = False) -> str:
    normalized = normalize_title_size(value, allow_inherit=allow_inherit)
    if normalized == "inherit":
        return "Как в личных настройках"
    return str(TITLE_SIZE_OPTIONS[normalized]["label"])


def title_size_scale(value: Any) -> float:
    normalized = normalize_title_size(value)
    return float(TITLE_SIZE_OPTIONS[normalized]["scale"])
