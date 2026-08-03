# Deckview agent guide

## Required workflow

Use the repository-local `$deckview-maintainer` skill for every change to this project.

Use the repository-local `$botforge` skill for every Telegram feature, handler,
middleware, navigation, or bot-architecture refactor. Deckview keeps its
existing storage and deployment stack unless a separate ADR explicitly changes
them, but all new Telegram code must follow BotForge's dependency direction:
`handlers -> services -> repositories/integrations`.

Codex may create or improve repository-local skills during work when a workflow is recurring, fragile, or needs a mandatory verification step. New skills must be created with the system `skill-creator`, live under `.agents/skills/`, pass its validator, and be committed with the behavior they document.

## Rendering skills

Use the repository-local `$deckview-pillow-renderer` for every Pillow,
NumPy, image layout, alpha, resize, crop, typography, JPEG, or visual
composition change.

Use `$deckview-visual-regression` for every change affecting rendered output.

Use `$deckview-render-cache` for prepared-card, render, or Telegram `file_id`
cache changes.

Use `$deckview-rust-compositor` for every change under `rust/deckview_core/`.

Use `$deckview-pyo3-boundary` for changes to the Python/Rust payload, PyO3
bindings, Maturin configuration, GIL handling, fallback, or wheel packaging.

Use `$deckview-render-contract` for every field or validation change in the
payload shared by Python and Rust.

Use `$deckview-render-benchmark` before claiming that a renderer or cache
optimization is faster.

Rust changes are incomplete unless all of these pass:

- `cargo fmt --check`
- `cargo clippy --all-targets --all-features -- -D warnings`
- `cargo test --all-features`
- `maturin build --release`
- Python import smoke test
- strict Pillow/Rust visual parity test

Production may fall back to Pillow. Tests, benchmarks, and CI must fail if
Rust unexpectedly falls back to Pillow.

## Architecture boundaries

- The repository root contains no Python modules. Runtime entrypoints are
  `python -m deckview`, `deckview.web.application:app`, and
  `python -m deckview.workers.worker`.
- `deckview/bot/`: Telegram lifecycle and router composition.
- `deckview/handlers/`: aiogram transport adapters.
- `deckview/services/`: transport-free use cases.
- `deckview/integrations/`: external Hearthstone and Manacost providers.
- `deckview/repositories/`: persistence.
- `deckview/workers/`, `deckview/infrastructure/`, `deckview/web/`: queue,
  cache/telemetry, and HTTP runtime respectively.
- `image_creator/`: deck resolution, card hydration, downloading, composition, and visual assets.
- Historical root compatibility modules are retired and must not be recreated.
- `framework/`, `db/`: shared infrastructure and database helpers.

Prefer extending the matching package module instead of creating root scripts
or duplicating renderer logic.

## Git delivery

After all required tests and render checks pass, push one atomic commit directly
to `main`. Do not create a feature branch or pull request unless the repository
owner explicitly requests one for that change.

## Definition of done

- Add focused regression coverage.
- Run the full test suite.
- Run `scripts/render_regression_decks.py` and inspect both the 30-card and 40-card Reno images.
- Never commit secrets, runtime databases, caches, logs, user uploads, backups, or generated regression images.
- Preserve unrelated local edits and worktrees.
