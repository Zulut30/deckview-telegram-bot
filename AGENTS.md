# Deckview agent guide

## Required workflow

Use the repository-local `$deckview-maintainer` skill for every change to this project.

Codex may create or improve repository-local skills during work when a workflow is recurring, fragile, or needs a mandatory verification step. New skills must be created with the system `skill-creator`, live under `.agents/skills/`, pass its validator, and be committed with the behavior they document.

## Architecture boundaries

- `main.py`: Telegram handlers and orchestration only.
- `image_creator/`: deck resolution, card hydration, downloading, composition, and visual assets.
- `deckview_queue.py`, `deckview_worker.py`, `deckview_jobs.py`: bounded background rendering.
- `render_cache.py`, `telegram_photo_cache.py`: deterministic render and Telegram delivery caches.
- `web_app.py`, `web_db.py`: HTTP API, dashboard, and persistence.
- `framework/`, `db/`: shared infrastructure and database helpers.

Prefer extending the matching module instead of growing `main.py` or duplicating renderer logic.

## Definition of done

- Add focused regression coverage.
- Run the full test suite.
- Run `scripts/render_regression_decks.py` and inspect both the 30-card and 40-card Reno images.
- Never commit secrets, runtime databases, caches, logs, user uploads, backups, or generated regression images.
- Preserve unrelated local edits and worktrees.
