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

The extension uses schema `1` and renderer contract
`deckview-native/0.2.0`. Card-cell cache keys include renderer version, source
size and modification time, dimensions, sideboard state, and card type.

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

Benchmarks set `DECKVIEW_RUST_REQUIRED=1`, assert the actual backend, and fail
instead of silently measuring Pillow. Generated images and manifests must stay
outside Git and be visually inspected under `$deckview-visual-regression`.

## Baseline measured on 2026-08-03

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

## Rollout boundary

The native path currently covers the default classic layout. Death Knight
runes, custom fonts and text sizes, parchment, custom backgrounds, custom
class art, and custom lower-area layouts intentionally remain on Pillow.
Do not enable the native flag in production until those expected fallbacks are
explicitly represented in routing and Python/Rust visual parity is accepted.

For CI and benchmarks, `DECKVIEW_RUST_REQUIRED=1` makes any attempted native
render failure fatal. Production may keep fallback behavior while the flag is
experimental.
