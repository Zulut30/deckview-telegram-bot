#!/usr/bin/env python3
"""Refresh Deckview's local Kolodahs metadata snapshot."""

from __future__ import annotations

import argparse
import json
import os

from deckview.config import MANACOST_PUBLIC_API_BASE_URL, MANACOST_PUBLIC_API_KEY
from image_creator.card_catalog_snapshot import (
    fetch_standard_dbf_ids,
    refresh_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--api-root", default="https://kolodahs.ru/api/v1")
    parser.add_argument("--locale", default="ruru")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    arena_origin = os.getenv(
        "MANACOST_PUBLIC_API_ORIGIN_URL",
        MANACOST_PUBLIC_API_BASE_URL,
    ).strip().rstrip("/")
    standard = fetch_standard_dbf_ids(
        api_root=f"{arena_origin}/api/v1",
        api_key=MANACOST_PUBLIC_API_KEY,
        timeout=args.timeout,
    )
    result = refresh_snapshot(
        target_path=args.target,
        api_root=args.api_root,
        locale=args.locale,
        page_size=args.page_size,
        timeout=args.timeout,
        standard_dbf_ids=standard["dbf_ids"],
        standard_revision=standard["revision"],
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
