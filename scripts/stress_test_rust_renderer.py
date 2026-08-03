#!/usr/bin/env python3
"""Load-test the opt-in Rust renderer across many decks and custom styles.

The script never enables Rust outside its own process. It requires the native
backend, rejects silent Pillow fallback, records per-stage timings, and writes
one representative JPEG per deck/style plus a JSON manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class DeckCase:
    slug: str
    title: str
    code: str


SCENARIOS: dict[str, dict[str, Any]] = {
    "classic": {
        "image_style": "classic",
        "image_font": "hearthstone",
        "image_text_size": "normal",
    },
    "parchment": {
        "image_style": "parchment",
        "image_font": "belwe",
        "image_text_size": "large",
        "image_dust_display": "large",
    },
    "custom-gradient": {
        "image_style": "custom",
        "image_background": {
            "kind": "gradient",
            "value": "#102A43,#7FDBFF",
            "blur": 0,
        },
        "image_font": "montserrat",
        "image_text_size": "xlarge",
    },
    "custom-minimal": {
        "image_style": "custom",
        "image_background": {
            "kind": "gradient",
            "value": "#19143F,#7F5BD5",
            "blur": 0,
        },
        "image_font": "roboto_slab",
        "image_text_size": "huge",
        "image_dust_display": "hidden",
        "image_mana_curve": {"mode": "hidden", "path": None},
    },
}


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _slug(value: str, fallback: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-"
        for character in str(value or "")
    )
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:80] or fallback


def _deck_from_value(value: Any, index: int) -> DeckCase:
    if isinstance(value, str):
        title = f"Deck {index + 1}"
        code = value.strip()
    elif isinstance(value, dict):
        title = str(value.get("title") or value.get("name") or f"Deck {index + 1}")
        code = str(value.get("code") or value.get("deck_code") or "").strip()
    else:
        raise ValueError(f"deck entry {index + 1} must be a string or object")
    if not code:
        raise ValueError(f"deck entry {index + 1} has no code")
    return DeckCase(_slug(title, f"deck-{index + 1}"), title, code)


def load_deck_cases(path: Path) -> list[DeckCase]:
    """Load JSON arrays/objects, JSONL, or plain ``title<TAB>code`` files."""
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"deck code file is empty: {path}")
    if path.suffix.lower() == ".json":
        decoded = json.loads(content)
        values = decoded.get("decks", []) if isinstance(decoded, dict) else decoded
        if not isinstance(values, list):
            raise ValueError("JSON deck file must contain a list or {'decks': [...]} object")
        return [_deck_from_value(value, index) for index, value in enumerate(values)]

    cases = []
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            value = json.loads(line)
        elif "\t" in line:
            title, code = line.split("\t", 1)
            value = {"title": title, "code": code}
        else:
            value = line
        cases.append(_deck_from_value(value, len(cases)))
    if not cases:
        raise ValueError(f"deck code file contains no usable entries: {path}")
    return cases


def selected_scenarios(value: str) -> list[str]:
    names = list(SCENARIOS) if value.strip().lower() == "all" else [
        item.strip() for item in value.split(",") if item.strip()
    ]
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
    if not names:
        raise ValueError("at least one scenario is required")
    return names


def scenario_settings(name: str, args: argparse.Namespace) -> dict[str, Any]:
    settings = deepcopy(SCENARIOS[name])
    if name == "custom-gradient" and args.custom_background:
        settings["image_background"] = {
            "kind": "image",
            "value": str(args.custom_background.resolve()),
            "blur": args.background_blur,
        }
    if args.custom_logo:
        settings["image_class_art"] = {
            "mode": "logo",
            "path": str(args.custom_logo.resolve()),
        }
    if name == "custom-minimal" and args.mana_image:
        settings["image_mana_curve"] = {
            "mode": "image",
            "path": str(args.mana_image.resolve()),
        }
    return settings


async def _save_sample(image, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)

    def save() -> int:
        image.convert("RGB").save(path, "JPEG", quality=90, optimize=True)
        return path.stat().st_size

    return await asyncio.to_thread(save)


async def run_stress(args: argparse.Namespace) -> dict[str, Any]:
    from deckview_core import cache_info, renderer_info
    from image_creator import create_picture
    from scripts.render_regression_decks import RENO_DECKS

    cases = (
        load_deck_cases(args.deck_codes)
        if args.deck_codes
        else [DeckCase(deck.slug, deck.title, deck.code) for deck in RENO_DECKS]
    )
    if args.limit:
        cases = cases[: args.limit]
    scenario_names = selected_scenarios(args.scenarios)
    semaphore = asyncio.Semaphore(args.concurrency)
    samples_written: set[tuple[str, str]] = set()
    samples_lock = asyncio.Lock()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    async def render_one(case: DeckCase, scenario_name: str, iteration: int) -> None:
        async with semaphore:
            timings: dict[str, Any] = {}
            started = time.perf_counter_ns()
            try:
                image, dust, deck_class, mode, card_ids = await create_picture(
                    case.code,
                    deck_name=case.title,
                    timings=timings,
                    **scenario_settings(scenario_name, args),
                )
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                if image is None:
                    raise AssertionError("renderer returned no image")
                backend = image.info.get("deckview_renderer")
                if backend != "rust":
                    raise AssertionError(f"expected rust backend, got {backend!r}")
                if max(image.size) > args.max_output_side:
                    raise AssertionError(
                        f"output {image.size} exceeds max side {args.max_output_side}"
                    )
                extrema = image.convert("RGB").getextrema()
                if all(low == high for low, high in extrema):
                    raise AssertionError("output is a single flat colour")

                sample_path = None
                output_bytes = None
                sample_key = (case.slug, scenario_name)
                async with samples_lock:
                    should_write = sample_key not in samples_written
                    if should_write:
                        samples_written.add(sample_key)
                if should_write:
                    path = args.output_dir / f"{case.slug}-{scenario_name}.jpg"
                    output_bytes = await _save_sample(image, path)
                    sample_path = str(path.resolve())
                results.append(
                    {
                        "deck": case.slug,
                        "scenario": scenario_name,
                        "iteration": iteration,
                        "elapsed_ms": round(elapsed_ms, 3),
                        "compose_ms": timings.get("image_compose_ms"),
                        "total_ms": timings.get("generator_total_ms"),
                        "size": list(image.size),
                        "cards": len(card_ids),
                        "dust": dust,
                        "class": deck_class,
                        "mode": mode,
                        "sample_path": sample_path,
                        "output_bytes": output_bytes,
                        "timings_ms": timings,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "deck": case.slug,
                        "scenario": scenario_name,
                        "iteration": iteration,
                        "error": type(exc).__name__,
                        "message": str(exc),
                        "timings_ms": timings,
                    }
                )
                if args.fail_fast:
                    raise

    jobs = [
        render_one(case, scenario, iteration)
        for iteration in range(1, args.repeat + 1)
        for case in cases
        for scenario in scenario_names
    ]
    await asyncio.gather(*jobs)

    elapsed = [float(result["elapsed_ms"]) for result in results]
    compose = [
        float(result["compose_ms"])
        for result in results
        if result.get("compose_ms") is not None
    ]
    manifest = {
        "renderer": renderer_info(),
        "native_cache": {
            "background_hits": cache_info()[0],
            "background_misses": cache_info()[1],
            "background_evictions": cache_info()[2],
            "background_entries": cache_info()[3],
        },
        "configuration": {
            "decks": len(cases),
            "scenarios": scenario_names,
            "repeat": args.repeat,
            "concurrency": args.concurrency,
            "jobs": len(jobs),
        },
        "summary": {
            "completed": len(results),
            "failed": len(errors),
            "elapsed_p50_ms": round(statistics.median(elapsed), 3) if elapsed else None,
            "elapsed_p95_ms": round(percentile(elapsed, 0.95), 3) if elapsed else None,
            "elapsed_max_ms": round(max(elapsed), 3) if elapsed else None,
            "compose_p50_ms": round(statistics.median(compose), 3) if compose else None,
            "compose_p95_ms": round(percentile(compose, 0.95), 3) if compose else None,
        },
        "results": results,
        "errors": errors,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "stress-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**manifest["configuration"], **manifest["summary"]}, ensure_ascii=False))
    print(f"manifest: {manifest_path.resolve()}")
    if errors:
        raise RuntimeError(f"{len(errors)} of {len(jobs)} Rust render jobs failed")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-codes", type=Path, help="JSON, JSONL, or text deck list")
    parser.add_argument("--limit", type=int, default=0, help="limit unique input decks")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--rust-threads", type=int, default=4)
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--custom-background", type=Path)
    parser.add_argument("--background-blur", type=int, choices=(0, 25, 50, 100), default=50)
    parser.add_argument("--custom-logo", type=Path)
    parser.add_argument("--mana-image", type=Path)
    parser.add_argument("--max-output-side", type=int, default=1920)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "rust-render-stress",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if not 1 <= args.concurrency <= 32:
        parser.error("--concurrency must be between 1 and 32")
    if not 1 <= args.rust_threads <= 16:
        parser.error("--rust-threads must be between 1 and 16")
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    for label, path in (
        ("--custom-background", args.custom_background),
        ("--custom-logo", args.custom_logo),
        ("--mana-image", args.mana_image),
    ):
        if path is not None and not path.is_file():
            parser.error(f"{label} does not point to a file: {path}")
    try:
        selected_scenarios(args.scenarios)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    os.environ["DECKVIEW_RUST_RENDER"] = "1"
    os.environ["DECKVIEW_RUST_RENDER_STRICT"] = "1"
    os.environ["DECKVIEW_RUST_REQUIRED"] = "1"
    os.environ["DECKVIEW_RUST_THREADS"] = str(args.rust_threads)
    asyncio.run(run_stress(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
