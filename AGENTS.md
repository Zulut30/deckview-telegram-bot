# Deckview agent guide

## Required workflow

Use the repository-local `$deckview-maintainer` skill for every change to this project.

Use the repository-local `$botforge` skill for every Telegram feature, handler,
middleware, navigation, or bot-architecture refactor. Deckview keeps its
existing storage and deployment stack unless a separate ADR explicitly changes
them, but all new Telegram code must follow BotForge's dependency direction:
`handlers -> services -> repositories/integrations`.

Codex may create or improve repository-local skills during work when a workflow is recurring, fragile, or needs a mandatory verification step. New skills must be created with the system `skill-creator`, live under `.agents/skills/`, pass its validator, and be committed with the behavior they document.

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
