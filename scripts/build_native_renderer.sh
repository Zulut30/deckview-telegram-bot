#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:-$project_root/dist/native}"
target_cpu="${DECKVIEW_RUST_TARGET_CPU:-native}"

mkdir -p "$output_dir"
cd "$project_root/rust/deckview_core"

export RUSTFLAGS="${RUSTFLAGS:-} -C target-cpu=$target_cpu"
exec maturin build --release --strip --locked --out "$output_dir"
