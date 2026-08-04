"""Stable download references for Telegram deck-image buttons."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping

from deckview.infrastructure.render_cache import lookup_render_cache_by_key


_CACHE_KEY_BYTES = 32
_ENCODED_REFERENCE_LENGTH = 43


def encode_render_download_reference(cache_key: object) -> str | None:
    """Encode a SHA-256 render key within Telegram's 64-byte callback limit."""
    normalized = str(cache_key or "").strip().lower()
    if len(normalized) != _CACHE_KEY_BYTES * 2 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        return None
    return base64.urlsafe_b64encode(bytes.fromhex(normalized)).rstrip(b"=").decode("ascii")


def decode_render_download_reference(reference: object) -> str | None:
    """Decode a render reference, failing closed for malformed callback data."""
    normalized = str(reference or "").strip()
    if len(normalized) != _ENCODED_REFERENCE_LENGTH or any(
        not (character.isalnum() or character in "-_") for character in normalized
    ):
        return None
    try:
        decoded = base64.b64decode(
            normalized + "=" * (-len(normalized) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        return None
    if len(decoded) != _CACHE_KEY_BYTES:
        return None
    return decoded.hex()


def build_download_reference(
    cache_entry: Mapping[str, Any] | None,
    *,
    fallback_reference: str,
) -> str:
    """Prefer the persistent cache artifact while retaining a fail-open fallback."""
    persistent = encode_render_download_reference(
        cache_entry.get("cache_key") if cache_entry else None
    )
    return persistent or str(fallback_reference)


def resolve_cached_download(reference: object) -> str | None:
    """Return the trusted persistent JPEG path represented by a callback token."""
    cache_key = decode_render_download_reference(reference)
    if cache_key is None:
        return None
    entry = lookup_render_cache_by_key(cache_key)
    if not entry:
        return None
    artifact_path = str(entry.get("artifact_path") or "").strip()
    return artifact_path or None
