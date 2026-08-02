# Deckview release checks

Use this checklist before a production deployment or Git push.

## Correctness

- Run the full unit-test suite.
- Render the canonical 30-card and 40-card Reno decks.
- Confirm every decoded main-deck card appears exactly once in a singleton grid.
- Confirm sideboard cards remain separate from the main deck.
- Inspect both images for equal card size, row alignment, spacing, title placement, class art, mana curve, and dust display.

## Performance

- Record `generator_total_ms` and each stage timing printed by the regression renderer.
- Verify a warm rerun uses local metadata, prepared-card, deck, and render caches.
- Check that no unbounded network fallback or retry loop was added to the request path.

## Operations

- Verify `deckview-bot`, `deckview-web`, and `deckview-worker` health when deployment is in scope.
- Keep API keys in environment variables or the deployment secret store.
- Exclude SQLite databases, Redis dumps, caches, uploads, logs, backups, `.env*`, and generated test artifacts from Git.
- Review the staged diff and secret scan before pushing.
