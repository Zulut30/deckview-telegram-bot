#!/usr/bin/env python3
"""Compare warm Pillow and experimental Rust deck composition on Reno fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from image_creator import create_picture  # noqa: E402
from image_creator.cards_placer import _clear_card_cell_cache  # noqa: E402
from scripts.render_regression_decks import RENO_DECKS, validate_deck  # noqa: E402


@contextmanager
def renderer_environment(backend: str, threads: int):
    keys = (
        "DECKVIEW_RUST_RENDER",
        "DECKVIEW_RUST_RENDER_STRICT",
        "DECKVIEW_RUST_REQUIRED",
        "DECKVIEW_RUST_THREADS",
        "DECKVIEW_FAST_PIL",
    )
    previous = {key: os.environ.get(key) for key in keys}
    is_rust = backend == "rust"
    os.environ["DECKVIEW_RUST_RENDER"] = "1" if is_rust else "0"
    os.environ["DECKVIEW_RUST_RENDER_STRICT"] = "1" if backend == "rust" else "0"
    os.environ["DECKVIEW_RUST_REQUIRED"] = "1" if backend == "rust" else "0"
    os.environ["DECKVIEW_RUST_THREADS"] = str(threads)
    os.environ["DECKVIEW_FAST_PIL"] = "1" if backend == "pillow-fast" else "0"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def benchmark_deck(deck, backend: str, iterations: int, threads: int, output_dir: Path):
    validate_deck(deck)
    samples = []
    image = None
    with renderer_environment(backend, threads):
        for _index in range(iterations):
            timings = {}
            image, _dust, _deck_class, _mode, _card_ids = await create_picture(
                deck.code,
                deck_name=deck.title,
                timings=timings,
                image_style="classic",
            )
            if image is None:
                raise RuntimeError(f"{backend}/{deck.slug}: renderer returned no image")
            actual_backend = image.info.get("deckview_renderer")
            expected_backend = "rust" if backend == "rust" else "pillow"
            if actual_backend != expected_backend:
                raise AssertionError(
                    f"{backend}/{deck.slug}: expected {expected_backend}, "
                    f"got {actual_backend!r}"
                )
            samples.append(float(timings["image_compose_ms"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{deck.slug}-{backend}.jpg"
    image.convert("RGB").save(output_path, "JPEG", quality=90, optimize=True)
    warm = samples[1:] if len(samples) > 1 else samples
    return {
        "backend": backend,
        "deck": deck.slug,
        "cards": deck.expected_cards,
        "iterations": iterations,
        "threads": threads if backend == "rust" else 1,
        "cold_ms": round(samples[0], 3),
        "warm_min_ms": round(min(warm), 3),
        "warm_median_ms": round(statistics.median(warm), 3),
        "warm_samples_ms": [round(value, 3) for value in warm],
        "size": list(image.size),
        "path": str(output_path.resolve()),
        "output_bytes": output_path.stat().st_size,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=6)
    parser.add_argument("--rust-threads", type=int, default=4)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "native-render-benchmark",
    )
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2 to measure a warm render")
    if not 1 <= args.rust_threads <= 16:
        parser.error("--rust-threads must be between 1 and 16")

    try:
        from deckview_core import clear_card_cache, renderer_info
    except ImportError as exc:
        raise SystemExit(
            "deckview_core is not installed; build the experimental wheel first"
        ) from exc

    results = []
    for deck in RENO_DECKS:
        for backend in ("pillow-legacy", "pillow-fast", "rust"):
            _clear_card_cell_cache()
            clear_card_cache()
            results.append(
                await benchmark_deck(
                    deck, backend, args.iterations, args.rust_threads, args.output_dir
                )
            )

    by_deck = {}
    for result in results:
        by_deck.setdefault(result["deck"], {})[result["backend"]] = result
    comparisons = []
    for deck, backends in by_deck.items():
        pillow_ms = backends["pillow-fast"]["warm_median_ms"]
        rust_ms = backends["rust"]["warm_median_ms"]
        comparisons.append(
            {
                "deck": deck,
                "pillow_legacy_cold_ms": backends["pillow-legacy"]["cold_ms"],
                "pillow_fast_cold_ms": backends["pillow-fast"]["cold_ms"],
                "rust_cold_ms": backends["rust"]["cold_ms"],
                "cold_speedup": round(
                    backends["pillow-fast"]["cold_ms"]
                    / backends["rust"]["cold_ms"],
                    2,
                ),
                "pillow_legacy_warm_median_ms": backends["pillow-legacy"][
                    "warm_median_ms"
                ],
                "pillow_fast_warm_median_ms": pillow_ms,
                "rust_warm_median_ms": rust_ms,
                "warm_speedup": round(pillow_ms / rust_ms, 2),
            }
        )

    manifest = {
        "native": renderer_info(),
        "results": results,
        "comparisons": comparisons,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
