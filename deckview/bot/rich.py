"""Small adapter for Telegram Bot API rich messages.

aiogram may lag behind newly released Bot API methods, so rich messages are
called through the raw HTTP API while the rest of the bot can stay on aiogram.
"""

from __future__ import annotations

import html
import time
from typing import Any

import aiohttp

from deckview.config import TELEGRAM_API_BASE_URL, TOKEN


class TelegramRichError(RuntimeError):
    """Raised when Telegram rejects a rich-message request."""


def _api_url(method: str) -> str:
    base = TELEGRAM_API_BASE_URL or "https://api.telegram.org"
    if not TOKEN:
        raise TelegramRichError("Telegram token is not configured")
    return f"{base.rstrip('/')}/bot{TOKEN}/{method}"


def _json_dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", exclude_none=True)
        except TypeError:
            return value.model_dump(exclude_none=True)
    if hasattr(value, "to_python"):
        return value.to_python()
    return value


async def _post_json(method: str, payload: dict[str, Any], timeout_seconds: float = 15.0) -> Any:
    clean_payload = {key: _json_dump(value) for key, value in payload.items() if value is not None}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(_api_url(method), json=clean_payload) as response:
            try:
                data = await response.json(content_type=None)
            except Exception as exc:
                body = await response.text()
                raise TelegramRichError(f"{method} HTTP {response.status}: {body[:240]}") from exc

    if not data.get("ok"):
        description = data.get("description") or f"HTTP {response.status}"
        raise TelegramRichError(f"{method}: {description}")
    return data.get("result")


async def send_rich_message(
    chat_id: int | str,
    rich_html: str,
    *,
    reply_markup: Any = None,
    message_thread_id: int | None = None,
    reply_parameters: Any = None,
) -> Any:
    return await _post_json(
        "sendRichMessage",
        {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "rich_message": {"html": rich_html, "skip_entity_detection": True},
            "reply_parameters": reply_parameters,
            "reply_markup": reply_markup,
        },
    )


async def edit_message_rich_text(
    chat_id: int | str,
    message_id: int,
    rich_html: str,
    *,
    reply_markup: Any = None,
) -> Any:
    return await _post_json(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": {"html": rich_html, "skip_entity_detection": True},
            "reply_markup": reply_markup,
        },
    )


async def send_rich_message_draft(
    chat_id: int,
    draft_id: int,
    rich_html: str,
    *,
    message_thread_id: int | None = None,
) -> bool:
    await _post_json(
        "sendRichMessageDraft",
        {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "draft_id": draft_id,
            "rich_message": {"html": rich_html, "skip_entity_detection": True},
        },
        timeout_seconds=8.0,
    )
    return True


async def send_thinking_draft(
    chat_id: int,
    text: str,
    *,
    message_thread_id: int | None = None,
) -> int:
    draft_id = int(time.time() * 1000) % 2_147_483_647
    if draft_id == 0:
        draft_id = 1
    escaped = html.escape(text, quote=False)
    await send_rich_message_draft(
        chat_id,
        draft_id,
        f"<tg-thinking>{escaped}</tg-thinking>",
        message_thread_id=message_thread_id,
    )
    return draft_id
