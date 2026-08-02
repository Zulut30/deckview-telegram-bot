"""Shared, bounded raw-RGBA cache for prepared deck-card cells."""

from __future__ import annotations

import hashlib
import os
import struct
import uuid
from pathlib import Path

from PIL import Image


_CACHE_VERSION = "rgba-v6-location-optical"
_MAGIC = b"DVCELL2\0"
# Cell/crop sizes are unsigned, but centered over-wide cards legitimately
# have a negative X offset.
_HEADER = struct.Struct(">8s2I2i2I")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _enabled() -> bool:
    return os.getenv("DECKVIEW_PREPARED_CARD_CACHE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cache_path(key: tuple) -> Path | None:
    if not _enabled():
        return None
    root_value = os.getenv(
        "DECKVIEW_PREPARED_CARD_CACHE_ROOT",
        "cache/prepared-cards",
    ).strip()
    root = Path(root_value)
    if not root.is_absolute():
        root = _PROJECT_ROOT / root
    digest = hashlib.sha256(
        repr((_CACHE_VERSION, key)).encode("utf-8")
    ).hexdigest()
    return root / digest[:2] / f"{digest}.rgba"


def load_prepared_card(
    key: tuple,
    expected_size: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    path = _cache_path(key)
    if path is None:
        return None
    try:
        payload = path.read_bytes()
        magic, width, height, ox, oy, card_width, card_height = _HEADER.unpack(
            payload[: _HEADER.size]
        )
        if magic != _MAGIC or (width, height) != expected_size:
            return None
        pixels = payload[_HEADER.size :]
        if len(pixels) != width * height * 4:
            return None
        cell = Image.frombytes("RGBA", (width, height), pixels)
        try:
            os.utime(path, None)
        except OSError:
            pass
        return cell, (ox, oy, card_width, card_height)
    except (OSError, ValueError, struct.error):
        return None


def _max_bytes() -> int:
    raw_bytes = os.getenv("DECKVIEW_PREPARED_CARD_CACHE_MAX_BYTES", "").strip()
    try:
        if raw_bytes:
            return max(1, int(raw_bytes))
        max_gb = float(
            os.getenv("DECKVIEW_PREPARED_CARD_CACHE_MAX_GB", "4").replace(
                ",",
                ".",
            )
        )
        return max(1, int(max_gb * 1024**3))
    except (TypeError, ValueError):
        return 4 * 1024**3


def _prune_prepared_card_shard(path: Path, max_bytes: int) -> None:
    try:
        entries = []
        total_size = 0
        for candidate in path.glob("*.rgba"):
            try:
                stat = candidate.stat()
            except OSError:
                continue
            total_size += stat.st_size
            entries.append((stat.st_mtime_ns, stat.st_size, candidate))
        if total_size <= max_bytes:
            return
        for _mtime, size, candidate in sorted(entries):
            try:
                candidate.unlink()
                total_size -= size
            except OSError:
                continue
            if total_size <= max_bytes:
                return
    except OSError:
        pass


def store_prepared_card(
    key: tuple,
    prepared: tuple[Image.Image, tuple[int, int, int, int]],
) -> None:
    path = _cache_path(key)
    if path is None:
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        cell, layout = prepared
        width, height = cell.size
        payload = _HEADER.pack(
            _MAGIC,
            width,
            height,
            *layout,
        ) + cell.tobytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        os.replace(temporary, path)
        _prune_prepared_card_shard(
            path.parent,
            max_bytes=max(1, _max_bytes() // 256),
        )
    except (OSError, ValueError, struct.error):
        # A cache write is never allowed to suppress a card from the render.
        pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
