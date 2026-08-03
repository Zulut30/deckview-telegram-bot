#!/usr/bin/env python3
"""Strict import and render smoke test for the experimental native wheel."""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from deckview_core import RenderContractError, render_deck_image, renderer_info

    payload = {
        "schema_version": 1,
        "renderer_version": "deckview-native/0.2.0",
        "cards": [
            {
                "path": str((ROOT / "assets" / "title.png").resolve()),
                "name": "Native smoke",
                "count": 1,
                "mana": 1,
                "is_side": False,
                "card_type": "SPELL",
            }
        ],
        "layout": {
            "cell_w": 375,
            "cell_h": 507,
            "row_gap": 72,
            "top_margin": 250,
            "bottom_margin": 800,
            "n_cols": 1,
        },
        "assets": {
            "water_path": str((ROOT / "assets" / "x2-white.png").resolve()),
            "dust_asset_path": str((ROOT / "assets" / "dust.png").resolve()),
            "class_asset_path": str((ROOT / "class" / "class_mage.png").resolve()),
            "font_path": str((ROOT / "HEARTHSTONE_CYRILLIC.ttf").resolve()),
            "allowed_roots": [str(ROOT.resolve())],
        },
        "output": {"max_output_side": 1920, "jpeg_quality": 92},
        "deck": {"cost": 40, "name": "Native smoke"},
    }
    encoded = render_deck_image(payload)
    image = Image.open(BytesIO(encoded))
    image.load()
    if image.format != "JPEG":
        raise AssertionError(f"expected JPEG, got {image.format!r}")
    if max(image.size) > 1920:
        raise AssertionError(f"native output exceeds MAX_OUTPUT_SIDE: {image.size}")

    unsupported = deepcopy(payload)
    unsupported["schema_version"] = 999
    try:
        render_deck_image(unsupported)
    except RenderContractError as exc:
        if "schema_version" not in str(exc):
            raise AssertionError(f"unclear schema error: {exc}") from exc
    else:
        raise AssertionError("unsupported schema_version was accepted")

    invalid_path = deepcopy(payload)
    invalid_path["cards"][0]["path"] = "/etc/passwd"
    try:
        render_deck_image(invalid_path)
    except RenderContractError as exc:
        if "allowed" not in str(exc) or "roots" not in str(exc):
            raise AssertionError(f"unclear path validation error: {exc}") from exc
    else:
        raise AssertionError("card path outside allowed roots was accepted")

    version, threads, _cached = renderer_info()
    if version != "deckview_core/0.2.0" or not 1 <= threads <= 16:
        raise AssertionError(f"unexpected renderer_info: {(version, threads)}")
    print(
        f"native smoke ok: renderer={version}, threads={threads}, "
        f"size={image.size}, bytes={len(encoded)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
