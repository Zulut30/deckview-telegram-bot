# ADR 0001: Modular BotForge architecture

- **Status:** accepted
- **BotForge:** 1.8.0
- **Date:** 2026-08-02

## Context

Deckview grew around root-level modules and a 6000-line `main.py`. Telegram
transport, business rules, persistence, external APIs and image rendering are
therefore coupled, making safe releases and isolated tests difficult.

## Decision

New and migrated Telegram features live under `deckview/` and follow this
dependency direction:

```text
handlers -> services -> repositories
                    -> integrations
handlers -> keyboards
```

Handlers only parse Telegram updates, call a service and render the reply.
Services contain use-case orchestration and do not import aiogram. External HTTP
providers live in `integrations`; persistence will move to `repositories`.

The implementation migration is complete. Existing root modules remain as thin
compatibility aliases for systemd, gunicorn, RQ jobs and downstream imports;
architecture tests prevent business logic from returning to them. Renderer and
database behavior are not changed by this ADR.

## Consequences

- Feature routers can be tested and released independently.
- Legacy import paths remain available without duplicating implementations.
- Temporary compatibility aliases add a small amount of duplication in the
  module namespace, but not in implementation.
- Architecture tests prevent new cross-layer imports.
