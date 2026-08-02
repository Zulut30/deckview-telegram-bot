"""Safe custom backgrounds shared by user and managed-chat deck themes."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from .card_showcase import parchment_background


_ROOT = Path(__file__).resolve().parent.parent
_BACKGROUND_ROOT = (_ROOT / "user_assets" / "backgrounds").resolve()
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
_BACKGROUND_CACHE_MAX = 4
_background_cache_lock = threading.RLock()
_background_cache: OrderedDict[tuple, Image.Image] = OrderedDict()
BLUR_STRENGTHS = (0, 25, 50, 100)

GRADIENT_PRESETS = OrderedDict(
    [
        ("tavern", ("🍺 Таверна", "#24140D,#B86B2F")),
        ("arcane", ("🔮 Аркана", "#19143F,#7F5BD5")),
        ("hearth", ("💙 Камень очага", "#10274A,#238BC5")),
        ("ember", ("🔥 Угли", "#33140D,#D9792B")),
        ("frost", ("❄️ Лёд", "#102A43,#7FDBFF")),
        ("fel", ("💚 Скверна", "#122314,#76C442")),
        ("emerald", ("🌿 Изумруд", "#102D26,#73C088")),
        ("twilight", ("🌌 Сумерки", "#1E163D,#A15BB4")),
        ("blood", ("🩸 Кровь", "#260B13,#B83D51")),
        ("steel", ("⚔️ Сталь", "#17212B,#778899")),
    ]
)


def normalize_gradient(value: str) -> str:
    colors = [part.strip() for part in str(value or "").split(",")]
    if len(colors) != 2 or not all(_HEX_RE.fullmatch(color) for color in colors):
        raise ValueError("Gradient must contain two #RRGGBB colors")
    return ",".join(color.upper() for color in colors)


def normalize_background_blur(value: object) -> int:
    """Normalize either 0..1 or percentage input to a supported blur level."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if 0 < number <= 1:
        number *= 100
    number = max(0.0, min(100.0, number))
    return min(BLUR_STRENGTHS, key=lambda level: abs(level - number))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def gradient_background(size: tuple[int, int], value: str) -> Image.Image:
    first, second = normalize_gradient(value).split(",")
    start = _hex_rgb(first)
    end = _hex_rgb(second)
    mask = Image.linear_gradient("L").resize(size, _RESAMPLE)
    return ImageOps.colorize(mask, start, end).convert("RGBA")


def _safe_background_path(value: str) -> Path:
    candidate = Path(str(value or ""))
    if not candidate.is_absolute():
        candidate = _ROOT / candidate
    resolved = candidate.resolve()
    if resolved != _BACKGROUND_ROOT and _BACKGROUND_ROOT not in resolved.parents:
        raise ValueError("Custom background path is outside the asset directory")
    if not resolved.is_file():
        raise FileNotFoundError("Custom background image is missing")
    return resolved


def _cached_background(key: tuple) -> Image.Image | None:
    with _background_cache_lock:
        cached = _background_cache.get(key)
        if cached is None:
            return None
        _background_cache.move_to_end(key)
        # The caller draws cards and footer elements directly onto the canvas.
        return cached.copy()


def _store_background(key: tuple, image: Image.Image) -> None:
    with _background_cache_lock:
        _background_cache[key] = image.copy()
        _background_cache.move_to_end(key)
        while len(_background_cache) > _BACKGROUND_CACHE_MAX:
            _background_cache.popitem(last=False)


def _clear_background_cache() -> None:
    with _background_cache_lock:
        _background_cache.clear()


def decorative_background(
    size: tuple[int, int],
    background: dict | None = None,
) -> Image.Image:
    """Build a cover-fitted custom background, falling back to parchment."""
    kind = str((background or {}).get("kind") or "").strip().lower()
    value = str((background or {}).get("value") or "").strip()
    if kind == "gradient":
        normalized_value = normalize_gradient(value)
        cache_key = ("gradient", tuple(size), normalized_value)
        cached = _cached_background(cache_key)
        if cached is not None:
            return cached
        base = gradient_background(size, normalized_value)
    elif kind == "image":
        path = _safe_background_path(value)
        stat = path.stat()
        blur = normalize_background_blur((background or {}).get("blur"))
        cache_key = (
            "image",
            tuple(size),
            str(path),
            stat.st_mtime_ns,
            stat.st_size,
            blur,
        )
        cached = _cached_background(cache_key)
        if cached is not None:
            return cached
        with Image.open(path) as source:
            base = ImageOps.fit(
                source.convert("RGB"),
                size,
                method=_RESAMPLE,
                centering=(0.5, 0.5),
            )
        if blur:
            # Scale radius with the target canvas: the four user-facing levels
            # remain visually comparable on both short and very large decks.
            radius = max(size) / 70 * (blur / 100)
            base = base.filter(ImageFilter.GaussianBlur(radius))
        base = base.convert("RGBA")
    else:
        return parchment_background(size)

    # Preserve the user's colors exactly. A former warm parchment wash was
    # especially visible on dark/blue uploads and made them look yellow.
    result = base.convert("RGBA")
    _store_background(cache_key, result)
    return result
