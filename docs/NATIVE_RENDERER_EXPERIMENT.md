# Experimental Rust renderer

Deckview contains an opt-in native compositor in `rust/deckview_core`. It is an
experiment and is **disabled by default**. Production continues to use Pillow.

## Stack

- PyO3 0.29.1 and Maturin 1.14.1 provide one batched Python → Rust call.
- `image` 0.25.10 decodes PNG, JPEG, and content-sniffed WebP card assets.
- `fast_image_resize` 6.1.0 performs Lanczos3 SIMD resizing.
- `ab_glyph` 0.2.32 and `imageproc` 0.27.0 draw text and chart elements.
- A bounded Rayon pool prepares cards concurrently. Set
  `DECKVIEW_RUST_THREADS`; the default is at most four threads per worker.

The extension uses schema `2` and renderer contract
`deckview-native/0.3.0`. It accepts the same style groups used by the bot:
classic/parchment/custom backgrounds, image blur, title font and size, dust
visibility/scale, class art or a logo, chart/hidden/image lower area, runes,
sideboards, and the managed layout selected by Python.

Card-cell and prepared-background cache keys include the renderer version and
source revision. A custom background is decoded, cover-fitted, and blurred
only on a cache miss; changing the source size or modification time invalidates
the entry. `deckview_core.cache_info()` exposes background hit, miss, eviction,
and entry counts for tests and telemetry.

## Build without production activation

Use an isolated virtual environment. Building with `target-cpu=native` makes
the wheel specific to the current CPU and unsuitable for redistribution to a
different processor.

```bash
python3 -m venv .native-venv
.native-venv/bin/pip install maturin==1.14.1
DECKVIEW_RUST_TARGET_CPU=native \
  .native-venv/bin/maturin build --release --locked \
  --manifest-path rust/deckview_core/Cargo.toml
```

The helper `scripts/build_native_renderer.sh` performs the same release build.
Installing a wheel does not enable it: `DECKVIEW_RUST_RENDER` remains `0`.

## Verification

Run the complete native gate:

```bash
cd rust/deckview_core
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features
cargo build --release
cd ../..
.native-venv/bin/python scripts/smoke_native_renderer.py
.native-venv/bin/python scripts/benchmark_render_backends.py \
  --iterations 8 --rust-threads 4 \
  --output-dir /tmp/deckview-native-benchmark
```

Run a strict multi-deck/style load test (the native wheel must already be
installed in the active environment):

```bash
python scripts/stress_test_rust_renderer.py \
  --deck-codes /path/to/decks.jsonl \
  --repeat 10 --concurrency 4 --scenarios all \
  --custom-background /path/to/background.jpg --background-blur 50 \
  --custom-logo /path/to/logo.png --mana-image /path/to/lower-area.png \
  --output-dir /tmp/deckview-rust-stress
```

The script sets strict Rust flags only for its own process, rejects Pillow
fallback, validates non-empty output and `MAX_OUTPUT_SIDE`, stores one visual
sample per deck/style, and records end-to-end plus per-stage p50/p95 timings.

Benchmarks set `DECKVIEW_RUST_REQUIRED=1`, assert the actual backend, and fail
instead of silently measuring Pillow. Generated images and manifests must stay
outside Git and be visually inspected under `$deckview-visual-regression`.

## Historical v0.2 baseline measured on 2026-08-03

The isolated benchmark used the same 30-card and 40-card Reno fixtures, six
iterations per backend, four native threads, and local prewarmed card sources.
Times below cover image composition; network and Telegram delivery are outside
this measurement.

| Deck | Backend | Cold | Warm median | JPEG bytes |
| --- | --- | ---: | ---: | ---: |
| 30 Reno | Pillow legacy | 346.1 ms | 134.3 ms | 922,759 |
| 30 Reno | Pillow fast | 311.7 ms | 132.8 ms | 922,759 |
| 30 Reno | Rust | 152.9 ms | 106.4 ms | 964,253 |
| 40 Reno | Pillow legacy | 689.7 ms | 138.5 ms | 865,887 |
| 40 Reno | Pillow fast | 358.9 ms | 135.5 ms | 865,887 |
| 40 Reno | Rust | 176.8 ms | 111.4 ms | 902,841 |

This prototype is about 2.0× faster than Pillow fast on a cold composition and
1.2–1.3× faster warm. It is not accepted for production yet: title/chart text
and curve styling are not visually identical, one 30-card output differs by one
pixel in height, and JPEG size grew by roughly 4–5%. The benchmark therefore
records promising latency without claiming completed visual parity.

## Schema v2 verification on 2026-08-03

The expanded renderer was measured again after adding the complete style
contract. The classic-only comparison was deliberately kept honest: Rust was
not generally faster than the optimized Pillow path in this short local run.

| Deck | Backend | Cold | Warm median |
| --- | --- | ---: | ---: |
| 30 Reno | Pillow fast | 156.2 ms | 139.6 ms |
| 30 Reno | Rust | 185.4 ms | 138.7 ms |
| 40 Reno | Pillow fast | 144.0 ms | 133.5 ms |
| 40 Reno | Rust | 203.5 ms | 144.9 ms |

An additional strict matrix rendered both canonical decks five times in each
of classic, parchment, custom-gradient, and custom-minimal modes: 40/40 jobs
completed through Rust, with compose p50 137.8 ms and p95 218.3 ms. The 30- and
40-card parchment outputs were opened and visually inspected. This validates
coverage and stability, but is not yet a reason to change the production flag.

## Rollout boundary

Schema v2 routes all current personalization groups to Rust when the opt-in
flag is enabled. This is still a test implementation: do not enable the native
flag in production until the full visual matrix (30/40 Reno, sideboard,
LOCATION, variable frame proportions, transparent artifacts, and all three
styles) is accepted and a current v0.3 Pillow/Rust benchmark is recorded.

For CI and benchmarks, `DECKVIEW_RUST_REQUIRED=1` makes any attempted native
render failure fatal. Production may keep fallback behavior while the flag is
experimental.
