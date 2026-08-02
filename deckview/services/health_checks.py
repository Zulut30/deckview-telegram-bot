from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import Any, Callable

from deckview.integrations.arena_stats import get_arena_matrix, get_arena_stats
from deckview.integrations.battlegrounds_stats import get_bgs_comps
from deckview.config import (
    API_TOKEN,
    BATTLE_NET_TOKEN,
    HS_DATA_API_BASE_URL,
    HS_DATA_API_ENABLED,
    TOKEN,
    WEB_DATABASE_PATH,
)
from deckview.integrations.hs_data_api import get_db_decks, get_meta_strategies


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}д {hours}ч {minutes}м"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _check(label: str, fn: Callable[[], str]) -> dict[str, Any]:
    start = time.monotonic()
    try:
        details = fn()
        ok = True
    except Exception as e:
        details = str(e)[:220] or e.__class__.__name__
        ok = False
    return {
        "label": label,
        "ok": ok,
        "ms": int((time.monotonic() - start) * 1000),
        "details": details,
    }


def _token_status(value: str | None) -> str:
    return "задан" if str(value or "").strip() else "нет"


def _cache_status() -> str:
    path = Path(WEB_DATABASE_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    parent = path.parent
    exists = path.exists()
    writable = os.access(parent, os.W_OK)
    return f"{'есть' if exists else 'нет'} · {'writable' if writable else 'read-only'} · {path.name}"


def run_health_checks(*, uptime_seconds: float | None = None) -> dict[str, Any]:
    checks = [
        _check("HS Data API / decks", lambda: f"{len(get_db_decks(limit=1, use_cache=False))} row"),
        _check("Meta Standard", lambda: f"{len(get_meta_strategies(1)[0])} rows"),
        _check("Meta Wild", lambda: f"{len(get_meta_strategies(2)[0])} rows"),
        _check("Arena tier", lambda: f"{len(get_arena_stats().get('stats') or [])} classes"),
        _check("Arena matrix", lambda: f"{len(get_arena_matrix().get('matchups') or [])} matchups"),
        _check("Battlegrounds comps", lambda: f"{len(get_bgs_comps('last-patch').get('comps') or [])} comps"),
        _check("Cache DB", _cache_status),
    ]
    return {
        "uptime": _duration(uptime_seconds or 0),
        "checks": checks,
        "config": {
            "bot_token": _token_status(TOKEN),
            "battle_net_token": _token_status(BATTLE_NET_TOKEN),
            "api_token": _token_status(API_TOKEN),
            "hs_data_api": "enabled" if HS_DATA_API_ENABLED else "disabled",
            "hs_data_api_base": HS_DATA_API_BASE_URL,
        },
    }


def format_health_message(data: dict[str, Any]) -> str:
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    ok_count = sum(1 for check in checks if check.get("ok"))
    total = len(checks)

    lines = [
        "🩺 <b>Deckview health</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⏱ Uptime: <b>{html.escape(str(data.get('uptime') or '0м'))}</b>",
        f"✅ Проверки: <b>{ok_count}/{total}</b>",
        "",
        "<b>Конфиг:</b>",
        f"• Bot token: <b>{html.escape(str(config.get('bot_token') or 'нет'))}</b>",
        f"• Battle.net token: <b>{html.escape(str(config.get('battle_net_token') or 'нет'))}</b>",
        f"• API token: <b>{html.escape(str(config.get('api_token') or 'нет'))}</b>",
        f"• HS Data API: <b>{html.escape(str(config.get('hs_data_api') or 'unknown'))}</b>",
        f"• Base: <code>{html.escape(str(config.get('hs_data_api_base') or ''))}</code>",
        "",
        "<b>Источники:</b>",
    ]
    for check in checks:
        mark = "✅" if check.get("ok") else "❌"
        label = html.escape(str(check.get("label") or "check"))
        ms = int(check.get("ms") or 0)
        details = html.escape(str(check.get("details") or ""))
        lines.append(f"{mark} <b>{label}</b> · {ms} ms · <i>{details}</i>")
    return "\n".join(lines)
