# ADR 0001: Modular BotForge architecture

- **Status:** accepted; compatibility-alias decision superseded by ADR 0002
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

The implementation migration is complete. At the time of this decision,
existing root modules remained as thin compatibility aliases. ADR 0002 later
retired those aliases after systemd, Gunicorn, RQ jobs and tests migrated to
canonical package imports. Renderer and database behavior are not changed by
this ADR.

## Consequences

- Feature routers can be tested and released independently.
- Legacy import paths were temporarily available during migration and were
  removed by ADR 0002.
- Architecture tests prevent new cross-layer imports.
