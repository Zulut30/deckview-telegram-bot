"""
Download latest HearthstoneJSON card databases.

Updates:
  - cards.json (enUS)
  - cardsRU.json (ruRU)
"""
from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path


BASE_URL = "https://api.hearthstonejson.com/v1/latest"
LANG_ENDPOINTS = {
    "cards.json": "enUS/cards.json",
    "cardsRU.json": "ruRU/cards.json",
}


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(dest.name + ".tmp")

    try:
        with urllib.request.urlopen(url) as response, open(tmp_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        tmp_path.replace(dest)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def update_cards(base_dir: Path) -> None:
    for filename, endpoint in LANG_ENDPOINTS.items():
        url = f"{BASE_URL}/{endpoint}"
        dest = base_dir / filename
        print(f"Downloading {url} -> {dest}")
        download_file(url, dest)
        print(f"Updated {dest}")


def main(argv: list[str]) -> int:
    base_dir = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent
    if not base_dir.exists():
        print(f"Base directory does not exist: {base_dir}", file=sys.stderr)
        return 1

    try:
        update_cards(base_dir)
    except Exception as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
