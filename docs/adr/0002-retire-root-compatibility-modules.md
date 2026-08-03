# ADR 0002: Retire root compatibility modules

- **Status:** accepted
- **BotForge:** 1.8.0
- **Date:** 2026-08-03
- **Supersedes:** compatibility-alias portion of ADR 0001

## Context

The first modular migration moved implementations under `deckview/`, but kept
29 root-level Python aliases. They preserved old imports while making the
repository still look flat and allowing new code to depend on obsolete module
names.

## Decision

Remove every root-level Python module. Canonical runtime entrypoints are:

```text
python -m deckview
gunicorn deckview.web.application:app
python -m deckview.workers.worker
```

Tests, deployment units and operational scripts import package modules
directly. Architecture checks fail if a Python file reappears at repository
root or production code imports a retired module name.

## Consequences

- Repository structure matches runtime architecture.
- There is one import path per implementation and no `sys.modules` aliasing.
- Downstream consumers using historical root imports must migrate to the
  documented `deckview.*` paths.
- Deploy units must be updated before a release containing this change is
  activated.
