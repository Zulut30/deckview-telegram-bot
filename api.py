"""
HTTP API для генерации изображений колод и панель администратора.
Использует ту же логику, что и бот, включая кэш.

Публичные endpoints (без авторизации):
  GET  /public/render?deck=<code>        — PNG изображение колоды
  GET  /public/meta?deck=<code>          — метаданные колоды (JSON)
  GET  /public/archetypes                — список всех переводов архетипов
  POST /public/archetypes/translate      — перевод названия колоды
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException, Response, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import config
import bot
from database import DeckDatabase
from wordpress import create_hs_deck_post
import hsguru_scraper

app = FastAPI(
    title="Manacost Deck API",
    description=(
        "API для работы с колодами Hearthstone.\n\n"
        "**Публичные endpoints** (префикс `/public/`) не требуют авторизации.\n"
        "**Приватные endpoints** требуют заголовок `X-API-Key` (если задан `API_KEY`)."
    ),
)

_db = DeckDatabase()

_ADMIN_HTML = Path(__file__).parent / "templates" / "admin.html"


def _auth(x_api_key: str | None) -> None:
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Public endpoints — no auth required
# ---------------------------------------------------------------------------

@app.get(
    "/public/render",
    tags=["Public"],
    summary="Получить PNG изображение колоды (без авторизации)",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def public_render(deck: str = Query(..., description="Код колоды (начинается с AAE)")):
    """Генерирует и возвращает PNG изображение колоды по коду. Не требует авторизации."""
    result = await bot.process_deck_string(deck)
    if not result:
        raise HTTPException(status_code=400, detail="Неверный код колоды")
    image_bytes, _ = result
    return Response(content=image_bytes.getvalue(), media_type="image/png")


@app.get(
    "/public/meta",
    tags=["Public"],
    summary="Получить метаданные колоды (без авторизации)",
)
async def public_meta(deck: str = Query(..., description="Код колоды (начинается с AAE)")):
    """Возвращает метаданные колоды (класс, формат, стоимость пыли и др.). Не требует авторизации."""
    result = await bot.process_deck_string(deck)
    if not result:
        raise HTTPException(status_code=400, detail="Неверный код колоды")
    _, metadata = result
    return metadata


class ArchetypeItem(BaseModel):
    eng: str
    rus: str


class TranslateRequest(BaseModel):
    name: str = ""


class TranslateResponse(BaseModel):
    original: str
    translated: str
    changed: bool


@app.get(
    "/public/archetypes",
    tags=["Public"],
    summary="Список всех переводов архетипов",
    response_model=List[ArchetypeItem],
)
async def public_archetypes_list():
    """
    Возвращает полный список пар (английское название → русское название) архетипов.
    Не требует авторизации.
    """
    pairs = hsguru_scraper.get_archetypes_list()
    return [{"eng": eng, "rus": rus} for eng, rus in pairs]


@app.post(
    "/public/archetypes/translate",
    tags=["Public"],
    summary="Перевести название колоды на русский",
    response_model=TranslateResponse,
)
async def public_archetypes_translate(body: TranslateRequest):
    """
    Переводит название колоды с английского на русский по таблице архетипов.
    Если перевод не найден — возвращает оригинал.
    Не требует авторизации.

    Пример запроса:
    ```json
    { "name": "Control Warrior" }
    ```
    """
    archetypes = hsguru_scraper.load_archetypes()
    translated = hsguru_scraper.translate_deck_name(body.name, archetypes)
    return {
        "original": body.name,
        "translated": translated,
        "changed": translated != body.name,
    }


# ---------------------------------------------------------------------------
# Private endpoints — require X-API-Key header (if API_KEY is configured)
# ---------------------------------------------------------------------------

@app.get("/render", tags=["Private"])
async def render(deck: str, x_api_key: str | None = Header(default=None)):
    """Возвращает PNG изображение колоды по коду."""
    _auth(x_api_key)
    result = await bot.process_deck_string(deck)
    if not result:
        raise HTTPException(status_code=400, detail="Bad deck code")
    image_bytes, _ = result
    return Response(content=image_bytes.getvalue(), media_type="image/png")


@app.get("/meta")
async def meta(deck: str, x_api_key: str | None = Header(default=None)):
    """Возвращает метаданные колоды."""
    _auth(x_api_key)
    result = await bot.process_deck_string(deck)
    if not result:
        raise HTTPException(status_code=400, detail="Bad deck code")
    _, metadata = result
    return metadata


class IngestPayload(BaseModel):
    deck_code: str
    deck_name: str
    streamer: str | None = None
    player: str | None = None
    dust: int | None = None
    source_url: str | None = None


@app.post("/ingest")
async def ingest(payload: IngestPayload, x_api_key: str | None = Header(default=None)):
    """Создает колоду в WordPress по данным парсера HSGuru."""
    _auth(x_api_key)
    result = await bot.process_deck_string(payload.deck_code)
    if not result:
        raise HTTPException(status_code=400, detail="Bad deck code")
    image_bytes, metadata = result
    dust_cost = payload.dust if payload.dust is not None else metadata.get("dust_cost", 0)
    ok = create_hs_deck_post(
        deck_code=payload.deck_code,
        deck_name=payload.deck_name,
        streamer=payload.streamer,
        player=payload.player,
        dust_cost=dust_cost,
        source_url=payload.source_url,
        image_bytes=image_bytes,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="WP create failed")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_panel(x_api_key: str | None = Header(default=None)):
    """Возвращает HTML панели администратора."""
    _auth(x_api_key)
    if not _ADMIN_HTML.exists():
        raise HTTPException(status_code=503, detail="Admin panel HTML not found")
    return HTMLResponse(_ADMIN_HTML.read_text(encoding="utf-8"))


@app.get("/admin/stats")
async def admin_stats(x_api_key: str | None = Header(default=None)):
    """Полная статистика бота для дашборда."""
    _auth(x_api_key)
    stats = _db.get_statistics()
    top_voted = _db.get_top_voted_decks(limit=5)
    return {**stats, "top_voted": top_voted}


@app.get("/admin/decks")
async def admin_decks(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    mode: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc"),
    x_api_key: str | None = Header(default=None),
):
    """Постраничный список всех колод."""
    _auth(x_api_key)
    return _db.get_all_decks(
        page=page,
        per_page=per_page,
        mode=mode or None,
        search=search or None,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@app.get("/admin/charts/daily")
async def admin_charts_daily(
    days: int = Query(default=30, ge=1, le=365),
    x_api_key: str | None = Header(default=None),
):
    """Количество новых колод по дням."""
    _auth(x_api_key)
    return _db.get_decks_per_day(days=days)


@app.get("/admin/charts/modes")
async def admin_charts_modes(x_api_key: str | None = Header(default=None)):
    """Распределение колод по режимам."""
    _auth(x_api_key)
    return _db.get_mode_distribution()


@app.get("/admin/charts/costs")
async def admin_charts_costs(x_api_key: str | None = Header(default=None)):
    """Распределение колод по стоимости пыли."""
    _auth(x_api_key)
    return _db.get_cost_distribution()


@app.get("/admin/schema")
async def admin_schema(x_api_key: str | None = Header(default=None)):
    """Метаданные схемы базы данных."""
    _auth(x_api_key)
    return _db.get_db_schema_info()
