"""
SQLite-хранилище для веб-приложения: генерации колод и библиотека.
Кэш: повторный запрос той же колоды (deck_code + deck_name) в течение WEB_CACHE_MAX_AGE_HOURS
возвращает существующий файл без повторной генерации.
"""
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from deckview.config import WEB_CACHE_MAX_AGE_HOURS, WEB_DATABASE_PATH

# Guard: init_db() выполняется только один раз за процесс
_db_initialized = False
IMAGE_STYLE_CLASSIC = "classic"
IMAGE_STYLE_PARCHMENT = "parchment"
IMAGE_STYLE_CUSTOM = "custom"
VALID_IMAGE_STYLES = {
    IMAGE_STYLE_CLASSIC,
    IMAGE_STYLE_PARCHMENT,
    IMAGE_STYLE_CUSTOM,
}
VALID_IMAGE_FONTS = {
    "auto",
    "hearthstone",
    "belwe",
    "montserrat",
    "oswald",
    "roboto_slab",
    "merriweather",
    "lato_black",
    "noto_serif",
    "inter",
    "open_sans",
    "roboto_condensed",
    "source_sans",
    "source_serif",
    "roboto",
}
VALID_BACKGROUND_BLURS = {0, 25, 50, 100}
TELEGRAM_GROUP_ANONYMOUS_BOT_ID = 1087968824


def _db_path() -> Path:
    p = Path(WEB_DATABASE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _get_conn():
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: несколько читателей не блокируют писателя, снижает "database is locked"
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Применяет все pending-миграции схемы (идемпотентно)."""
    # Список колонок, которые добавляем если ещё нет
    migrations = [
        ("generated_decks", "source",    "TEXT DEFAULT 'web'"),
        ("generated_decks", "deck_class", "TEXT"),
        ("generated_decks", "deck_mode",  "TEXT"),
        ("generated_decks", "user_id",    "INTEGER"),
        ("bot_users", "image_style", "TEXT NOT NULL DEFAULT 'classic'"),
        ("bot_users", "custom_background_kind", "TEXT"),
        ("bot_users", "custom_background_value", "TEXT"),
        ("bot_users", "custom_background_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "image_font", "TEXT NOT NULL DEFAULT 'auto'"),
        ("bot_users", "image_text_size", "TEXT NOT NULL DEFAULT 'normal'"),
        ("bot_users", "image_dust_display", "TEXT NOT NULL DEFAULT 'normal'"),
        ("bot_users", "class_art_mode", "TEXT NOT NULL DEFAULT 'class'"),
        ("bot_users", "custom_logo_path", "TEXT"),
        ("bot_users", "personalization_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "custom_background_blur", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "cards_per_row_normal", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "cards_per_row_extended", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "cards_per_row_highlander", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_users", "mana_curve_mode", "TEXT NOT NULL DEFAULT 'chart'"),
        ("bot_users", "mana_curve_image_path", "TEXT"),
        ("managed_chats", "custom_background_blur", "INTEGER NOT NULL DEFAULT 0"),
        ("managed_chats", "image_text_size", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("managed_chats", "image_font", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("managed_chats", "image_dust_display", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("managed_chats", "class_art_mode", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("managed_chats", "custom_logo_path", "TEXT"),
        ("managed_chats", "personalization_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("managed_chats", "cards_per_row_normal", "INTEGER NOT NULL DEFAULT -1"),
        ("managed_chats", "cards_per_row_extended", "INTEGER NOT NULL DEFAULT -1"),
        ("managed_chats", "cards_per_row_highlander", "INTEGER NOT NULL DEFAULT -1"),
        ("managed_chats", "mana_curve_mode", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("managed_chats", "mana_curve_image_path", "TEXT"),
        ("managed_chats", "deck_button_layout", "TEXT NOT NULL DEFAULT 'full'"),
        ("saved_image_designs", "cards_per_row_normal", "INTEGER NOT NULL DEFAULT 0"),
        ("saved_image_designs", "cards_per_row_extended", "INTEGER NOT NULL DEFAULT 0"),
        ("saved_image_designs", "cards_per_row_highlander", "INTEGER NOT NULL DEFAULT 0"),
        ("saved_image_designs", "mana_curve_mode", "TEXT NOT NULL DEFAULT 'chart'"),
        ("saved_image_designs", "mana_curve_image_path", "TEXT"),
    ]
    for table, column, col_def in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
    # Индекс по user_id создаём после того как колонка гарантированно существует
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generated_user_id ON generated_decks(user_id)"
    )


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет, и применяет миграции. Идемпотентна: повторный вызов — no-op."""
    global _db_initialized
    if _db_initialized:
        return
    _db_initialized = True
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS generated_decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_code TEXT NOT NULL,
                deck_name TEXT,
                cost INTEGER NOT NULL,
                filename TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                source TEXT DEFAULT 'web',
                deck_class TEXT,
                deck_mode TEXT,
                user_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_generated_deck_code ON generated_decks(deck_code);
            CREATE INDEX IF NOT EXISTS idx_generated_created_at ON generated_decks(created_at);

            CREATE TABLE IF NOT EXISTS archetype_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name_en TEXT NOT NULL,
                name_ru TEXT NOT NULL,
                hero_class TEXT,
                format TEXT NOT NULL,
                winrate REAL,
                game_count INTEGER NOT NULL DEFAULT 0,
                popularity TEXT,
                snapshot_date TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(name_en, format, snapshot_date)
            );
            CREATE INDEX IF NOT EXISTS idx_archetype_stats_format_date
                ON archetype_stats(format, snapshot_date DESC);

            CREATE TABLE IF NOT EXISTS deck_cards (
                generated_deck_id INTEGER NOT NULL REFERENCES generated_decks(id) ON DELETE CASCADE,
                dbf_id INTEGER NOT NULL,
                PRIMARY KEY (generated_deck_id, dbf_id)
            );
            CREATE INDEX IF NOT EXISTS idx_deck_cards_dbf_id ON deck_cards(dbf_id);

            CREATE TABLE IF NOT EXISTS library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                deck_code TEXT,
                added_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_added_at ON library(added_at);

            CREATE TABLE IF NOT EXISTS bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                chat_type TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bot_events_created_at ON bot_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_bot_events_type ON bot_events(event_type);

            CREATE TABLE IF NOT EXISTS user_saved_decks (
                user_id INTEGER NOT NULL,
                generated_deck_id INTEGER NOT NULL REFERENCES generated_decks(id) ON DELETE CASCADE,
                saved_at TEXT NOT NULL,
                PRIMARY KEY (user_id, generated_deck_id)
            );

            CREATE INDEX IF NOT EXISTS idx_user_saved_decks_user ON user_saved_decks(user_id);

            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manacost_identity_links (
                telegram_user_id INTEGER PRIMARY KEY,
                manacost_user_id TEXT NOT NULL UNIQUE,
                public_profile_id TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                display_name TEXT NOT NULL,
                has_access INTEGER NOT NULL DEFAULT 0,
                subscription_source TEXT NOT NULL DEFAULT '',
                subscription_checked_at TEXT,
                subscription_stale INTEGER NOT NULL DEFAULT 0,
                entitlements_json TEXT NOT NULL DEFAULT '{}',
                linked_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_manacost_identity_public_profile
                ON manacost_identity_links(public_profile_id);

            CREATE TABLE IF NOT EXISTS managed_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                chat_type TEXT NOT NULL DEFAULT '',
                added_by INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                image_style TEXT NOT NULL DEFAULT 'inherit',
                custom_background_kind TEXT,
                custom_background_value TEXT,
                custom_background_revision INTEGER NOT NULL DEFAULT 0,
                custom_background_blur INTEGER NOT NULL DEFAULT 0,
                disabled_commands TEXT NOT NULL DEFAULT '[]',
                deck_button_layout TEXT NOT NULL DEFAULT 'full',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_managed_chats_added_by
                ON managed_chats(added_by, is_active);

            CREATE TABLE IF NOT EXISTS managed_chat_managers (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_managed_chat_managers_user
                ON managed_chat_managers(user_id, chat_id);

            CREATE TABLE IF NOT EXISTS saved_image_designs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                image_style TEXT NOT NULL DEFAULT 'classic',
                custom_background_kind TEXT,
                custom_background_value TEXT,
                custom_background_blur INTEGER NOT NULL DEFAULT 0,
                image_font TEXT NOT NULL DEFAULT 'auto',
                image_text_size TEXT NOT NULL DEFAULT 'normal',
                image_dust_display TEXT NOT NULL DEFAULT 'normal',
                class_art_mode TEXT NOT NULL DEFAULT 'class',
                custom_logo_path TEXT,
                cards_per_row_normal INTEGER NOT NULL DEFAULT 0,
                cards_per_row_extended INTEGER NOT NULL DEFAULT 0,
                cards_per_row_highlander INTEGER NOT NULL DEFAULT 0,
                mana_curve_mode TEXT NOT NULL DEFAULT 'chart',
                mana_curve_image_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_saved_image_designs_user
                ON saved_image_designs(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS publish_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_name TEXT,
                deck_class TEXT,
                deck_mode TEXT,
                deck_code TEXT,
                telegram_sent INTEGER NOT NULL DEFAULT 0,
                wordpress_posted INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_publish_logs_created_at ON publish_logs(created_at);

            CREATE TABLE IF NOT EXISTS archetypes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name_en TEXT NOT NULL UNIQUE,
                name_ru TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        _migrate(conn)
        # Anonymous group-admin messages are represented by Telegram's
        # service bot, not by the real administrator. Never treat that service
        # identity as the owner of a managed chat.
        conn.execute(
            "UPDATE managed_chats SET added_by = NULL WHERE added_by = ?",
            (TELEGRAM_GROUP_ANONYMOUS_BOT_ID,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO managed_chat_managers (
                chat_id, user_id, created_at, updated_at
            )
            SELECT chat_id, added_by, created_at, updated_at
            FROM managed_chats
            WHERE added_by IS NOT NULL
            """
        )


def ensure_bot_user(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    """Учесть пользователя бота. Обновляет last_seen при каждом вызове (при /start и при каждой генерации колоды)."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM bot_users WHERE user_id = ?", (user_id,))
        if cur.fetchone():
            conn.execute(
                "UPDATE bot_users SET last_seen = ?, username = ?, first_name = ? WHERE user_id = ?",
                (now, username or "", first_name or "", user_id),
            )
        else:
            conn.execute(
                "INSERT INTO bot_users (user_id, username, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                (user_id, username or "", first_name or "", now, now),
            )


def get_bot_users_count() -> int:
    """Число уникальных пользователей (кто хотя бы раз открыл бота в личке)."""
    with _get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM bot_users")
        row = cur.fetchone()
    return row[0] if row else 0


def get_all_bot_user_ids() -> List[int]:
    """Список user_id всех пользователей для рассылки."""
    with _get_conn() as conn:
        cur = conn.execute("SELECT user_id FROM bot_users ORDER BY last_seen DESC")
        rows = cur.fetchall()
    return [r["user_id"] for r in rows]


def get_manacost_identity(telegram_user_id: int) -> Dict[str, Any] | None:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM manacost_identity_links
            WHERE telegram_user_id = ?
            """,
            (int(telegram_user_id),),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["has_access"] = bool(result.get("has_access"))
    result["subscription_stale"] = bool(result.get("subscription_stale"))
    try:
        entitlements = json.loads(result.get("entitlements_json") or "{}")
        result["entitlements"] = (
            entitlements if isinstance(entitlements, dict) else {}
        )
    except (TypeError, ValueError):
        result["entitlements"] = {}
    return result


def save_manacost_identity(
    telegram_user_id: int,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist verified public profile data, never OAuth credentials."""
    required = (
        "manacost_user_id",
        "public_profile_id",
        "profile_url",
        "display_name",
    )
    normalized = {
        key: str(profile.get(key) or "").strip()
        for key in required
    }
    if any(not normalized[key] for key in required):
        raise ValueError("Incomplete Manacost identity")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO manacost_identity_links (
                    telegram_user_id, manacost_user_id, public_profile_id,
                    profile_url, display_name, has_access,
                    subscription_source, subscription_checked_at,
                    subscription_stale, entitlements_json,
                    linked_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    manacost_user_id = excluded.manacost_user_id,
                    public_profile_id = excluded.public_profile_id,
                    profile_url = excluded.profile_url,
                    display_name = excluded.display_name,
                    has_access = excluded.has_access,
                    subscription_source = excluded.subscription_source,
                    subscription_checked_at = excluded.subscription_checked_at,
                    subscription_stale = excluded.subscription_stale,
                    entitlements_json = excluded.entitlements_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(telegram_user_id),
                    normalized["manacost_user_id"][:200],
                    normalized["public_profile_id"][:200],
                    normalized["profile_url"][:1000],
                    normalized["display_name"][:200],
                    1 if profile.get("has_access") else 0,
                    str(profile.get("subscription_source") or "")[:100],
                    (
                        str(profile.get("subscription_checked_at") or "")[:80]
                        or None
                    ),
                    1 if profile.get("subscription_stale") else 0,
                    json.dumps(
                        profile.get("entitlements") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            "This Manacost identity is already linked"
        ) from exc
    result = get_manacost_identity(telegram_user_id)
    if not result:
        raise RuntimeError("Manacost identity was not saved")
    return result


def remove_manacost_identity(telegram_user_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            DELETE FROM manacost_identity_links
            WHERE telegram_user_id = ?
            """,
            (int(telegram_user_id),),
        )
        return bool(cursor.rowcount)


def normalize_user_image_style(value: Any) -> str:
    style = str(value or IMAGE_STYLE_CLASSIC).strip().lower()
    return style if style in VALID_IMAGE_STYLES else IMAGE_STYLE_CLASSIC


def normalize_user_image_font(value: Any) -> str:
    font = str(value or "auto").strip().lower()
    return font if font in VALID_IMAGE_FONTS else "auto"


def normalize_user_image_text_size(value: Any, *, allow_inherit: bool = False) -> str:
    from image_creator.text_size import normalize_title_size

    return normalize_title_size(value, allow_inherit=allow_inherit)


def normalize_background_blur(value: Any) -> int:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if 0 < number <= 1:
        number *= 100
    number = max(0.0, min(100.0, number))
    return min(VALID_BACKGROUND_BLURS, key=lambda level: abs(level - number))


def get_user_image_style(user_id: int) -> str:
    """Return the saved deck-image style, falling back to the classic design."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT image_style FROM bot_users WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    return normalize_user_image_style(row["image_style"] if row else None)


def set_user_image_style(user_id: int, image_style: str) -> str:
    """Persist a validated deck-image style and return its normalized value."""
    style = normalize_user_image_style(image_style)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                image_style, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                image_style = excluded.image_style,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, style),
        )
    return style


def get_user_image_settings(user_id: int) -> Dict[str, Any]:
    """Return the complete render theme for a user."""
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT image_style, image_font, image_text_size,
                   image_dust_display, class_art_mode, custom_logo_path,
                   personalization_revision,
                   custom_background_kind,
                   custom_background_value, custom_background_revision,
                   custom_background_blur,
                   cards_per_row_normal, cards_per_row_extended,
                   cards_per_row_highlander, mana_curve_mode,
                   mana_curve_image_path
            FROM bot_users WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()
    if not row:
        return {
            "style": IMAGE_STYLE_CLASSIC,
            "font": "auto",
            "text_size": "normal",
            "dust_display": "normal",
            "class_art_mode": "class",
            "custom_logo_path": None,
            "personalization_revision": 0,
            "background_kind": None,
            "background_value": None,
            "revision": 0,
            "blur": 0,
            "cards_per_row_normal": 0,
            "cards_per_row_extended": 0,
            "cards_per_row_highlander": 0,
            "mana_curve_mode": "chart",
            "mana_curve_image_path": None,
        }
    from image_creator.personalization import (
        normalize_class_art_mode,
        normalize_cards_per_row,
        normalize_dust_display,
        normalize_mana_curve_mode,
    )

    return {
        "style": normalize_user_image_style(row["image_style"]),
        "font": normalize_user_image_font(row["image_font"]),
        "text_size": normalize_user_image_text_size(row["image_text_size"]),
        "dust_display": normalize_dust_display(row["image_dust_display"]),
        "class_art_mode": normalize_class_art_mode(row["class_art_mode"]),
        "custom_logo_path": row["custom_logo_path"],
        "personalization_revision": int(row["personalization_revision"] or 0),
        "background_kind": row["custom_background_kind"],
        "background_value": row["custom_background_value"],
        "revision": int(row["custom_background_revision"] or 0),
        "blur": normalize_background_blur(row["custom_background_blur"]),
        "cards_per_row_normal": normalize_cards_per_row(
            row["cards_per_row_normal"]
        ),
        "cards_per_row_extended": normalize_cards_per_row(
            row["cards_per_row_extended"]
        ),
        "cards_per_row_highlander": normalize_cards_per_row(
            row["cards_per_row_highlander"]
        ),
        "mana_curve_mode": normalize_mana_curve_mode(row["mana_curve_mode"]),
        "mana_curve_image_path": row["mana_curve_image_path"],
    }


def normalize_image_design_name(name: str) -> str:
    """Validate a short user-facing preset name."""
    normalized = " ".join(str(name or "").strip().split())
    if not normalized:
        raise ValueError("Design name is empty")
    if len(normalized) > 32:
        raise ValueError("Design name is too long")
    return normalized


def _save_image_design_snapshot(
    user_id: int,
    name: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_name = normalize_image_design_name(name)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        existing = next(
            (
                row
                for row in conn.execute(
                    """
                    SELECT id, name FROM saved_image_designs
                    WHERE user_id = ?
                    """,
                    (int(user_id),),
                ).fetchall()
                if str(row["name"]).casefold() == normalized_name.casefold()
            ),
            None,
        )
        values = (
            normalized_name,
            settings["style"],
            settings.get("background_kind"),
            settings.get("background_value"),
            normalize_background_blur(settings.get("blur")),
            normalize_user_image_font(settings.get("font")),
            normalize_user_image_text_size(settings.get("text_size")),
            settings.get("dust_display") or "normal",
            settings.get("class_art_mode") or "class",
            settings.get("custom_logo_path"),
            int(settings.get("cards_per_row_normal") or 0),
            int(settings.get("cards_per_row_extended") or 0),
            int(settings.get("cards_per_row_highlander") or 0),
            settings.get("mana_curve_mode") or "chart",
            settings.get("mana_curve_image_path"),
        )
        if existing:
            conn.execute(
                """
                UPDATE saved_image_designs
                SET name = ?, image_style = ?,
                    custom_background_kind = ?,
                    custom_background_value = ?,
                    custom_background_blur = ?, image_font = ?,
                    image_text_size = ?, image_dust_display = ?,
                    class_art_mode = ?, custom_logo_path = ?,
                    cards_per_row_normal = ?, cards_per_row_extended = ?,
                    cards_per_row_highlander = ?, mana_curve_mode = ?,
                    mana_curve_image_path = ?,
                    updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (*values, now, int(existing["id"]), int(user_id)),
            )
            design_id = int(existing["id"])
        else:
            cursor = conn.execute(
                """
                INSERT INTO saved_image_designs (
                    name, image_style, custom_background_kind,
                    custom_background_value, custom_background_blur,
                    image_font, image_text_size, image_dust_display,
                    class_art_mode, custom_logo_path,
                    cards_per_row_normal, cards_per_row_extended,
                    cards_per_row_highlander, mana_curve_mode,
                    mana_curve_image_path,
                    created_at, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values, now, now, int(user_id)),
            )
            design_id = int(cursor.lastrowid)
        row = conn.execute(
            """
            SELECT * FROM saved_image_designs
            WHERE user_id = ? AND id = ?
            """,
            (int(user_id), design_id),
        ).fetchone()
    return dict(row)


def save_user_image_design(user_id: int, name: str) -> Dict[str, Any]:
    """Create or replace a named snapshot of all personal image settings."""
    return _save_image_design_snapshot(
        user_id,
        name,
        get_user_image_settings(user_id),
    )


def save_managed_chat_image_design(
    user_id: int,
    chat_id: int,
    name: str,
) -> Dict[str, Any]:
    """Save the effective design of one managed chat as a reusable preset."""
    chat = get_managed_chat(chat_id)
    if not chat:
        raise ValueError("Managed chat was not found")
    user = get_user_image_settings(user_id)
    style = str(chat.get("image_style") or "inherit")
    if style == "inherit":
        resolved_style = user["style"]
        background_kind = user.get("background_kind")
        background_value = user.get("background_value")
        blur = user.get("blur", 0)
    elif style == "custom":
        resolved_style = "custom"
        background_kind = chat.get("custom_background_kind")
        background_value = chat.get("custom_background_value")
        blur = chat.get("custom_background_blur", 0)
    else:
        resolved_style = style
        background_kind = None
        background_value = None
        blur = 0
    font = str(chat.get("image_font") or "inherit")
    text_size = str(chat.get("image_text_size") or "inherit")
    dust = str(chat.get("image_dust_display") or "inherit")
    class_art = str(chat.get("class_art_mode") or "inherit")
    row_settings = {}
    for category in ("normal", "extended", "highlander"):
        key = f"cards_per_row_{category}"
        chat_value = int(chat.get(key) if chat.get(key) is not None else -1)
        row_settings[key] = user.get(key, 0) if chat_value == -1 else chat_value
    curve_mode = str(chat.get("mana_curve_mode") or "inherit")
    if curve_mode == "inherit":
        resolved_curve_mode = user.get("mana_curve_mode") or "chart"
        curve_path = user.get("mana_curve_image_path")
    else:
        resolved_curve_mode = curve_mode
        curve_path = chat.get("mana_curve_image_path")
    if class_art == "inherit":
        resolved_class_art = user.get("class_art_mode") or "class"
        logo_path = user.get("custom_logo_path")
    else:
        resolved_class_art = class_art
        logo_path = chat.get("custom_logo_path")
    snapshot = {
        "style": resolved_style,
        "background_kind": background_kind,
        "background_value": background_value,
        "blur": blur,
        "font": user.get("font") if font == "inherit" else font,
        "text_size": (
            user.get("text_size")
            if text_size == "inherit"
            else text_size
        ),
        "dust_display": (
            user.get("dust_display") if dust == "inherit" else dust
        ),
        "class_art_mode": resolved_class_art,
        "custom_logo_path": logo_path,
        **row_settings,
        "mana_curve_mode": resolved_curve_mode,
        "mana_curve_image_path": curve_path,
    }
    return _save_image_design_snapshot(user_id, name, snapshot)


def get_user_image_designs(
    user_id: int,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM saved_image_designs
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(50, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def apply_user_image_design(
    user_id: int,
    design_id: int,
) -> Dict[str, Any] | None:
    """Apply one owned preset atomically and invalidate all render caches."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        design = conn.execute(
            """
            SELECT * FROM saved_image_designs
            WHERE id = ? AND user_id = ?
            """,
            (int(design_id), int(user_id)),
        ).fetchone()
        if not design:
            return None
        conn.execute(
            """
            UPDATE bot_users
            SET image_style = ?,
                custom_background_kind = ?,
                custom_background_value = ?,
                custom_background_blur = ?,
                image_font = ?,
                image_text_size = ?,
                image_dust_display = ?,
                class_art_mode = ?,
                custom_logo_path = ?,
                cards_per_row_normal = ?,
                cards_per_row_extended = ?,
                cards_per_row_highlander = ?,
                mana_curve_mode = ?,
                mana_curve_image_path = ?,
                custom_background_revision =
                    COALESCE(custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                last_seen = ?
            WHERE user_id = ?
            """,
            (
                design["image_style"],
                design["custom_background_kind"],
                design["custom_background_value"],
                normalize_background_blur(
                    design["custom_background_blur"]
                ),
                design["image_font"],
                design["image_text_size"],
                design["image_dust_display"],
                design["class_art_mode"],
                design["custom_logo_path"],
                design["cards_per_row_normal"],
                design["cards_per_row_extended"],
                design["cards_per_row_highlander"],
                design["mana_curve_mode"],
                design["mana_curve_image_path"],
                now,
                int(user_id),
            ),
        )
    return get_user_image_settings(user_id)


def delete_user_image_design(user_id: int, design_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            DELETE FROM saved_image_designs
            WHERE id = ? AND user_id = ?
            """,
            (int(design_id), int(user_id)),
        )
    return cursor.rowcount > 0


def set_user_custom_background(user_id: int, kind: str, value: str) -> Dict[str, Any]:
    """Save a validated custom background and activate the custom style."""
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"gradient", "image"}:
        raise ValueError("Unsupported background kind")
    normalized_value = str(value or "").strip()
    if not normalized_value:
        raise ValueError("Background value is empty")
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                image_style, custom_background_kind,
                custom_background_value, custom_background_revision,
                personalization_revision
            ) VALUES (?, '', '', ?, ?, 'custom', ?, ?, 1, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                image_style = 'custom',
                custom_background_kind = excluded.custom_background_kind,
                custom_background_value = excluded.custom_background_value,
                custom_background_revision =
                    COALESCE(bot_users.custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized_kind, normalized_value),
        )
    return get_user_image_settings(user_id)


def set_user_image_font(user_id: int, image_font: str) -> str:
    """Persist the title font used by new deck images."""
    font = normalize_user_image_font(image_font)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                image_font, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                image_font = excluded.image_font,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, font),
        )
    return font


def set_user_image_text_size(user_id: int, image_text_size: str) -> str:
    """Persist the deck-title size used by new deck images."""
    text_size = normalize_user_image_text_size(image_text_size)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                image_text_size, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                image_text_size = excluded.image_text_size,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, text_size),
        )
    return text_size


def set_user_cards_per_row(user_id: int, category: str, value: Any) -> int:
    from image_creator.personalization import normalize_cards_per_row

    columns = {
        "normal": "cards_per_row_normal",
        "extended": "cards_per_row_extended",
        "highlander": "cards_per_row_highlander",
    }
    if category not in columns:
        raise ValueError("Unsupported deck layout category")
    normalized = normalize_cards_per_row(value)
    now = datetime.now(timezone.utc).isoformat()
    column = columns[category]
    with _get_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                {column}, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                {column} = excluded.{column},
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized),
        )
    return normalized


def set_user_mana_curve_mode(user_id: int, mode: str) -> str:
    from image_creator.personalization import normalize_mana_curve_mode

    normalized = normalize_mana_curve_mode(mode)
    current = get_user_image_settings(user_id)
    if normalized == "image" and not current.get("mana_curve_image_path"):
        raise ValueError("Mana curve image is not uploaded")
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                mana_curve_mode, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                mana_curve_mode = excluded.mana_curve_mode,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized),
        )
    return normalized


def set_user_mana_curve_image(user_id: int, path: str) -> Dict[str, Any]:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("Mana curve image path is empty")
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                mana_curve_mode, mana_curve_image_path,
                personalization_revision
            ) VALUES (?, '', '', ?, ?, 'image', ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                mana_curve_mode = 'image',
                mana_curve_image_path = excluded.mana_curve_image_path,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized_path),
        )
    return get_user_image_settings(user_id)


def set_user_dust_display(user_id: int, display: str) -> str:
    """Persist the dust-cost presentation used by new deck images."""
    from image_creator.personalization import normalize_dust_display

    normalized = normalize_dust_display(display)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                image_dust_display, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                image_dust_display = excluded.image_dust_display,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized),
        )
    return normalized


def set_user_class_art_mode(user_id: int, mode: str) -> str:
    """Switch between the Hearthstone class art and an uploaded logo."""
    from image_creator.personalization import normalize_class_art_mode

    normalized = normalize_class_art_mode(mode)
    current = get_user_image_settings(user_id)
    if normalized == "logo" and not current.get("custom_logo_path"):
        raise ValueError("Custom logo is not uploaded")
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                class_art_mode, personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                class_art_mode = excluded.class_art_mode,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized),
        )
    return normalized


def set_user_custom_logo(user_id: int, path: str) -> Dict[str, Any]:
    """Save an uploaded logo path and activate it for deck renders."""
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("Logo path is empty")
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                class_art_mode, custom_logo_path, personalization_revision
            ) VALUES (?, '', '', ?, ?, 'logo', ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                class_art_mode = 'logo',
                custom_logo_path = excluded.custom_logo_path,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, normalized_path),
        )
    return get_user_image_settings(user_id)


def set_user_background_blur(user_id: int, strength: Any) -> int:
    """Persist blur for an uploaded custom background and bust render caches."""
    blur = normalize_background_blur(strength)
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (
                user_id, username, first_name, first_seen, last_seen,
                custom_background_blur, custom_background_revision,
                personalization_revision
            ) VALUES (?, '', '', ?, ?, ?, 1, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                custom_background_blur = excluded.custom_background_blur,
                custom_background_revision =
                    COALESCE(bot_users.custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(bot_users.personalization_revision, 0) + 1,
                last_seen = excluded.last_seen
            """,
            (int(user_id), now, now, blur),
        )
    return blur


def clear_user_custom_background(user_id: int) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE bot_users
            SET image_style = 'parchment',
                custom_background_kind = NULL,
                custom_background_value = NULL,
                custom_background_blur = 0,
                custom_background_revision =
                    COALESCE(custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1
            WHERE user_id = ?
            """,
            (int(user_id),),
        )


def register_managed_chat(
    chat_id: int,
    title: str,
    chat_type: str,
    added_by: int | None,
    *,
    is_active: bool = True,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    manager_id = (
        int(added_by)
        if added_by is not None
        and int(added_by) != TELEGRAM_GROUP_ANONYMOUS_BOT_ID
        else None
    )
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO managed_chats (
                chat_id, title, chat_type, added_by, is_active,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                chat_type = excluded.chat_type,
                added_by = COALESCE(excluded.added_by, managed_chats.added_by),
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                int(chat_id),
                str(title or "").strip(),
                str(chat_type or "").strip(),
                manager_id,
                1 if is_active else 0,
                now,
                now,
            ),
        )
        if manager_id is not None:
            conn.execute(
                """
                INSERT INTO managed_chat_managers (
                    chat_id, user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (int(chat_id), manager_id, now, now),
            )


def get_managed_chat(chat_id: int) -> Dict[str, Any] | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM managed_chats WHERE chat_id = ?",
            (int(chat_id),),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["disabled_commands"] = list(
            json.loads(result.get("disabled_commands") or "[]")
        )
    except (TypeError, ValueError):
        result["disabled_commands"] = []
    return result


def get_managed_chats_for_user(user_id: int) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT managed_chats.*
            FROM managed_chats
            WHERE managed_chats.is_active = 1
              AND (
                    managed_chats.added_by = ?
                    OR EXISTS (
                        SELECT 1
                        FROM managed_chat_managers
                        WHERE managed_chat_managers.chat_id =
                                managed_chats.chat_id
                          AND managed_chat_managers.user_id = ?
                    )
              )
            ORDER BY managed_chats.updated_at DESC
            """,
            (int(user_id), int(user_id)),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["disabled_commands"] = list(
                json.loads(item.get("disabled_commands") or "[]")
            )
        except (TypeError, ValueError):
            item["disabled_commands"] = []
        result.append(item)
    return result


def set_managed_chat_image_style(chat_id: int, image_style: str) -> str:
    raw_style = str(image_style or "").strip().lower()
    style = raw_style if raw_style in {*VALID_IMAGE_STYLES, "inherit"} else "inherit"
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET image_style = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (style, datetime.now(timezone.utc).isoformat(), int(chat_id)),
        )
    return style


def set_managed_chat_custom_background(
    chat_id: int, kind: str, value: str
) -> Dict[str, Any] | None:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"gradient", "image"}:
        raise ValueError("Unsupported background kind")
    normalized_value = str(value or "").strip()
    if not normalized_value:
        raise ValueError("Background value is empty")
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET image_style = 'custom',
                custom_background_kind = ?,
                custom_background_value = ?,
                custom_background_revision =
                    COALESCE(custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized_kind,
                normalized_value,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return get_managed_chat(chat_id)


def set_managed_chat_background_blur(chat_id: int, strength: Any) -> int:
    """Persist blur for a managed chat's uploaded background."""
    blur = normalize_background_blur(strength)
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET custom_background_blur = ?,
                custom_background_revision =
                    COALESCE(custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                blur,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return blur


def set_managed_chat_image_text_size(chat_id: int, image_text_size: str) -> str:
    """Persist a title-size override for one managed group chat."""
    text_size = normalize_user_image_text_size(
        image_text_size,
        allow_inherit=True,
    )
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET image_text_size = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                text_size,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return text_size


def set_managed_chat_cards_per_row(
    chat_id: int, category: str, value: Any
) -> int:
    from image_creator.personalization import normalize_cards_per_row

    columns = {
        "normal": "cards_per_row_normal",
        "extended": "cards_per_row_extended",
        "highlander": "cards_per_row_highlander",
    }
    if category not in columns:
        raise ValueError("Unsupported deck layout category")
    normalized = normalize_cards_per_row(value, allow_inherit=True)
    column = columns[category]
    with _get_conn() as conn:
        conn.execute(
            f"""
            UPDATE managed_chats
            SET {column} = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (normalized, datetime.now(timezone.utc).isoformat(), int(chat_id)),
        )
    return normalized


def set_managed_chat_mana_curve_mode(chat_id: int, mode: str) -> str:
    from image_creator.personalization import normalize_mana_curve_mode

    normalized = normalize_mana_curve_mode(mode, allow_inherit=True)
    current = get_managed_chat(chat_id)
    if normalized == "image" and not (
        current and current.get("mana_curve_image_path")
    ):
        raise ValueError("Mana curve image is not uploaded")
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET mana_curve_mode = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (normalized, datetime.now(timezone.utc).isoformat(), int(chat_id)),
        )
    return normalized


def set_managed_chat_mana_curve_image(
    chat_id: int, path: str
) -> Dict[str, Any] | None:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("Mana curve image path is empty")
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET mana_curve_mode = 'image', mana_curve_image_path = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized_path,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return get_managed_chat(chat_id)


def set_managed_chat_deck_button_layout(chat_id: int, layout: str) -> str:
    from deckview.keyboards.deck_actions import normalize_deck_button_layout

    normalized = normalize_deck_button_layout(layout)
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET deck_button_layout = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return normalized


def set_managed_chat_image_font(chat_id: int, image_font: str) -> str:
    raw = str(image_font or "").strip().lower()
    font = (
        "inherit"
        if raw == "inherit"
        else normalize_user_image_font(raw)
    )
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET image_font = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                font,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return font


def set_managed_chat_dust_display(chat_id: int, display: str) -> str:
    from image_creator.personalization import normalize_dust_display

    raw = str(display or "").strip().lower()
    normalized = (
        "inherit"
        if raw == "inherit"
        else normalize_dust_display(raw)
    )
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET image_dust_display = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return normalized


def set_managed_chat_class_art_mode(chat_id: int, mode: str) -> str:
    raw = str(mode or "").strip().lower()
    normalized = raw if raw in {"inherit", "class", "logo"} else "inherit"
    current = get_managed_chat(chat_id)
    if normalized == "logo" and not (
        current and current.get("custom_logo_path")
    ):
        raise ValueError("Custom logo is not uploaded")
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET class_art_mode = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return normalized


def set_managed_chat_custom_logo(
    chat_id: int,
    path: str,
) -> Dict[str, Any] | None:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("Logo path is empty")
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET class_art_mode = 'logo',
                custom_logo_path = ?,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                normalized_path,
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return get_managed_chat(chat_id)


def apply_user_image_design_to_chat(
    user_id: int,
    design_id: int,
    chat_id: int,
) -> Dict[str, Any] | None:
    """Apply one of an admin's saved designs as explicit chat settings."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        design = conn.execute(
            """
            SELECT * FROM saved_image_designs
            WHERE id = ? AND user_id = ?
            """,
            (int(design_id), int(user_id)),
        ).fetchone()
        chat = conn.execute(
            "SELECT 1 FROM managed_chats WHERE chat_id = ?",
            (int(chat_id),),
        ).fetchone()
        if not design or not chat:
            return None
        conn.execute(
            """
            UPDATE managed_chats
            SET image_style = ?,
                custom_background_kind = ?,
                custom_background_value = ?,
                custom_background_blur = ?,
                image_font = ?,
                image_text_size = ?,
                image_dust_display = ?,
                class_art_mode = ?,
                custom_logo_path = ?,
                cards_per_row_normal = ?,
                cards_per_row_extended = ?,
                cards_per_row_highlander = ?,
                mana_curve_mode = ?,
                mana_curve_image_path = ?,
                custom_background_revision =
                    COALESCE(custom_background_revision, 0) + 1,
                personalization_revision =
                    COALESCE(personalization_revision, 0) + 1,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (
                design["image_style"],
                design["custom_background_kind"],
                design["custom_background_value"],
                normalize_background_blur(
                    design["custom_background_blur"]
                ),
                design["image_font"],
                design["image_text_size"],
                design["image_dust_display"],
                design["class_art_mode"],
                design["custom_logo_path"],
                design["cards_per_row_normal"],
                design["cards_per_row_extended"],
                design["cards_per_row_highlander"],
                design["mana_curve_mode"],
                design["mana_curve_image_path"],
                now,
                int(chat_id),
            ),
        )
    return get_managed_chat(chat_id)


def set_managed_chat_disabled_commands(
    chat_id: int, commands: List[str]
) -> List[str]:
    normalized = sorted(
        {
            str(command or "").strip().lower().lstrip("/")
            for command in commands
            if str(command or "").strip()
        }
    )
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE managed_chats
            SET disabled_commands = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (
                json.dumps(normalized, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
                int(chat_id),
            ),
        )
    return normalized


def is_managed_chat_command_enabled(chat_id: int, command: str) -> bool:
    chat = get_managed_chat(chat_id)
    if not chat or not chat.get("is_active"):
        return True
    normalized = str(command or "").strip().lower().lstrip("/")
    return normalized not in set(chat.get("disabled_commands") or [])


def save_deck_for_user(user_id: int, generated_deck_id: int) -> bool:
    """Сохранить колоду в профиль пользователя. True — добавлено, False — уже было."""
    with _get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO user_saved_decks (user_id, generated_deck_id, saved_at) VALUES (?, ?, ?)",
                (user_id, generated_deck_id, datetime.now(timezone.utc).isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_saved_deck(user_id: int, generated_deck_id: int) -> bool:
    """Удалить колоду из профиля пользователя. True если запись удалена."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM user_saved_decks WHERE user_id = ? AND generated_deck_id = ?",
            (user_id, generated_deck_id),
        )
        return cur.rowcount > 0


def get_saved_decks_for_user(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Список колод, сохранённых пользователем, по убыванию времени сохранения."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT g.id, g.deck_code, g.deck_name, g.cost, g.deck_class, g.deck_mode,
                   g.filename, g.created_at, s.saved_at
            FROM user_saved_decks s
            JOIN generated_decks g ON g.id = s.generated_deck_id
            WHERE s.user_id = ?
            ORDER BY s.saved_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "deck_code": r["deck_code"],
            "deck_name": r["deck_name"] or None,
            "cost": r["cost"],
            "deck_class": r["deck_class"] or None,
            "deck_mode": r["deck_mode"] or None,
            "filename": r["filename"],
            "created_at": r["created_at"],
            "saved_at": r["saved_at"],
        }
        for r in rows
    ]


def find_cached(deck_code: str, deck_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Ищет недавнюю генерацию для пары (deck_code, deck_name).
    None и пустая строка для deck_name считаются одним значением.
    Возвращает dict с ключами filename, cost, deck_code, deck_name или None.
    """
    if not deck_code:
        return None
    name_norm = (deck_name or "").strip() or None
    name_for_db = name_norm if name_norm else ""
    since = (datetime.now(timezone.utc) - timedelta(hours=WEB_CACHE_MAX_AGE_HOURS)).isoformat() if WEB_CACHE_MAX_AGE_HOURS > 0 else "1970-01-01"

    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT filename, cost, deck_code, deck_name, created_at
            FROM generated_decks
            WHERE deck_code = ?
              AND COALESCE(deck_name, '') = ?
              AND created_at >= ?
              AND (
                    source IS NULL
                    OR source NOT LIKE 'api:%'
                    OR source IN ('api', 'api:classic')
                  )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (deck_code, name_for_db, since),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "filename": row["filename"],
        "cost": row["cost"],
        "deck_code": row["deck_code"],
        "deck_name": row["deck_name"] or None,
    }


def add_generated(
    deck_code: str,
    deck_name: Optional[str],
    cost: int,
    filename: str,
    source: str = "web",
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """Сохраняет запись о сгенерированной колоде. Возвращает id созданной записи."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO generated_decks
               (deck_code, deck_name, cost, filename, created_at, source, deck_class, deck_mode, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_code,
                (deck_name or "").strip() or None,
                cost,
                filename,
                now,
                source,
                (deck_class or "").strip() or None,
                (deck_mode or "").strip() or None,
                user_id,
            ),
        )
        return cur.lastrowid


def deck_code_exists(deck_code: str) -> bool:
    """Return whether a generated deck with the exact code already exists."""
    normalized = str(deck_code or "").strip()
    if not normalized:
        return False
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM generated_decks WHERE deck_code = ? LIMIT 1",
            (normalized,),
        ).fetchone()
    return row is not None


def upsert_archetype_stats(stats: List[Dict[str, Any]]) -> int:
    """Persist one HSGuru archetype snapshot and return the number of rows."""
    if not stats:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in stats:
        name_en = str(item.get("name_en") or "").strip()
        name_ru = str(item.get("name_ru") or name_en).strip()
        deck_format = str(item.get("format") or "").strip()
        snapshot_date = str(item.get("snapshot_date") or "").strip()
        if not name_en or not deck_format or not snapshot_date:
            continue
        rows.append(
            (
                name_en,
                name_ru,
                str(item.get("hero_class") or "").strip() or None,
                deck_format,
                item.get("winrate"),
                max(0, int(item.get("game_count") or 0)),
                str(item.get("popularity") or "").strip() or None,
                snapshot_date,
                now,
            )
        )
    if not rows:
        return 0
    with _get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO archetype_stats (
                name_en, name_ru, hero_class, format, winrate,
                game_count, popularity, snapshot_date, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name_en, format, snapshot_date) DO UPDATE SET
                name_ru = excluded.name_ru,
                hero_class = excluded.hero_class,
                winrate = excluded.winrate,
                game_count = excluded.game_count,
                popularity = excluded.popularity,
                updated_at = excluded.updated_at
            """,
            rows,
        )
    return len(rows)


def add_deck_cards(generated_deck_id: int, dbf_ids: List[int]) -> None:
    """Сохраняет состав колоды (dbfId карт) для поиска по карте."""
    if not dbf_ids:
        return
    with _get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO deck_cards (generated_deck_id, dbf_id) VALUES (?, ?)",
            [(generated_deck_id, int(d)) for d in dbf_ids],
        )


def add_generated_with_cards(
    deck_code: str,
    deck_name: Optional[str],
    cost: int,
    filename: str,
    dbf_ids: List[int],
    source: str = "web",
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """Атомарно сохраняет колоду и её состав. Возвращает id созданной записи."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO generated_decks
               (deck_code, deck_name, cost, filename, created_at, source, deck_class, deck_mode, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_code,
                (deck_name or "").strip() or None,
                cost,
                filename,
                now,
                source,
                (deck_class or "").strip() or None,
                (deck_mode or "").strip() or None,
                user_id,
            ),
        )
        gen_id = cur.lastrowid
        if dbf_ids:
            conn.executemany(
                "INSERT OR IGNORE INTO deck_cards (generated_deck_id, dbf_id) VALUES (?, ?)",
                [(gen_id, int(d)) for d in dbf_ids],
            )
        return gen_id


def find_decks_containing_card(
    dbf_id: int, limit: int = 1, offset: int = 0
) -> List[Dict[str, Any]]:
    """Колоды (из сгенерированных в боте/вебе), в которых есть карта с данным dbfId. По убыванию created_at."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT g.id, g.deck_code, g.deck_name, g.cost, g.deck_class, g.deck_mode,
                   g.filename, g.created_at, g.source
            FROM generated_decks g
            JOIN deck_cards c ON c.generated_deck_id = g.id
            WHERE c.dbf_id = ?
            ORDER BY g.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(dbf_id), limit, offset),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "deck_code": r["deck_code"],
            "deck_name": r["deck_name"] or None,
            "cost": r["cost"],
            "deck_class": r["deck_class"] or None,
            "deck_mode": r["deck_mode"] or None,
            "filename": r["filename"],
            "created_at": r["created_at"],
            "source": r["source"] or "web",
        }
        for r in rows
    ]


def find_decks_containing_card_count(dbf_id: int) -> int:
    """Число колод, в которых есть карта с данным dbfId."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT COUNT(DISTINCT g.id)
            FROM generated_decks g
            JOIN deck_cards c ON c.generated_deck_id = g.id
            WHERE c.dbf_id = ?
            """,
            (int(dbf_id),),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def find_decks_with_all_cards(
    dbf_ids: List[int], limit: int = 10, offset: int = 0
) -> List[Dict[str, Any]]:
    """Колоды, в которых есть все перечисленные карты (по dbf_id). Пустой dbf_ids — 0 колод. По убыванию created_at."""
    if not dbf_ids:
        return []
    n = len(dbf_ids)
    placeholders = ",".join("?" * n)
    with _get_conn() as conn:
        cur = conn.execute(
            f"""
            SELECT g.id, g.deck_code, g.deck_name, g.cost, g.deck_class, g.deck_mode,
                   g.filename, g.created_at, g.source
            FROM generated_decks g
            INNER JOIN (
                SELECT generated_deck_id
                FROM deck_cards
                WHERE dbf_id IN ({placeholders})
                GROUP BY generated_deck_id
                HAVING COUNT(DISTINCT dbf_id) = ?
            ) sub ON sub.generated_deck_id = g.id
            ORDER BY g.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (*[int(x) for x in dbf_ids], n, limit, offset),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "deck_code": r["deck_code"],
            "deck_name": r["deck_name"] or None,
            "cost": r["cost"],
            "deck_class": r["deck_class"] or None,
            "deck_mode": r["deck_mode"] or None,
            "filename": r["filename"],
            "created_at": r["created_at"],
            "source": r["source"] or "web",
        }
        for r in rows
    ]


def find_decks_with_all_cards_count(dbf_ids: List[int]) -> int:
    """Число колод, содержащих все перечисленные карты (dbf_id). Пустой список — 0."""
    if not dbf_ids:
        return 0
    n = len(dbf_ids)
    placeholders = ",".join("?" * n)
    with _get_conn() as conn:
        cur = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT generated_deck_id
                FROM deck_cards
                WHERE dbf_id IN ({placeholders})
                GROUP BY generated_deck_id
                HAVING COUNT(DISTINCT dbf_id) = ?
            ) sub
            """,
            (*[int(x) for x in dbf_ids], n),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def get_deck_by_id(generated_deck_id: int) -> Optional[Dict[str, Any]]:
    """Колода по id записи в generated_decks."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, deck_code, deck_name, cost, filename, created_at, source, deck_class, deck_mode, user_id
            FROM generated_decks WHERE id = ?
            """,
            (int(generated_deck_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "deck_code": row["deck_code"],
        "deck_name": row["deck_name"] or None,
        "cost": row["cost"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "source": row["source"] or "web",
        "deck_class": row["deck_class"] or None,
        "deck_mode": row["deck_mode"] or None,
        "user_id": row["user_id"],
    }


def get_history(limit: int = 12) -> List[Dict[str, Any]]:
    """Последние сгенерированные колоды для истории на главной."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT filename, created_at
            FROM generated_decks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [{"name": r["filename"], "time": r["created_at"]} for r in rows]


def add_to_library(filename: str, deck_code: Optional[str] = None) -> None:
    """Добавляет запись в библиотеку."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO library (filename, deck_code, added_at) VALUES (?, ?, ?)",
            (filename, deck_code or None, now),
        )


# --- События бота для дашборда ---

def add_bot_event(event_type: str, chat_type: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    """Записать событие бота (команда, генерация колоды, ошибка)."""
    now = datetime.now(timezone.utc).isoformat()
    payload_str = json.dumps(payload, ensure_ascii=False) if payload else None
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_events (event_type, chat_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (event_type, chat_type or "", payload_str, now),
        )


def get_bot_events(
    limit: int = 100,
    offset: int = 0,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """События бота для логов. since — ISO дата (включительно)."""
    with _get_conn() as conn:
        q = """
            SELECT id, event_type, chat_type, payload, created_at
            FROM bot_events
            WHERE 1=1
        """
        params: List[Any] = []
        if event_type:
            q += " AND event_type = ?"
            params.append(event_type)
        if since:
            q += " AND created_at >= ?"
            params.append(since)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = conn.execute(q, params)
        rows = cur.fetchall()
    out = []
    for r in rows:
        try:
            pl = json.loads(r["payload"]) if r["payload"] else None
        except Exception:
            pl = r["payload"]
        out.append({
            "id": r["id"],
            "event_type": r["event_type"],
            "chat_type": r["chat_type"] or "",
            "payload": pl,
            "created_at": r["created_at"],
        })
    return out


def get_bot_events_count(event_type: Optional[str] = None, since: Optional[str] = None) -> int:
    """Число событий (для пагинации)."""
    with _get_conn() as conn:
        q = "SELECT COUNT(*) FROM bot_events WHERE 1=1"
        params: List[Any] = []
        if event_type:
            q += " AND event_type = ?"
            params.append(event_type)
        if since:
            q += " AND created_at >= ?"
            params.append(since)
        cur = conn.execute(q, params)
        row = cur.fetchone()
    return row[0] if row else 0


def get_admin_overview() -> Dict[str, Any]:
    """Полная статистика для панели администратора."""
    with _get_conn() as conn:
        def _one(q, *p):
            return conn.execute(q, p).fetchone()[0] or 0

        total_decks   = _one("SELECT COUNT(*) FROM generated_decks")
        today_decks   = _one("SELECT COUNT(*) FROM generated_decks WHERE DATE(created_at) = DATE('now')")
        week_decks    = _one("SELECT COUNT(*) FROM generated_decks WHERE created_at >= datetime('now','-7 days')")
        total_users   = _one("SELECT COUNT(*) FROM bot_users")
        total_events  = _one("SELECT COUNT(*) FROM bot_events")
        deck_events   = _one("SELECT COUNT(*) FROM bot_events WHERE event_type='deck_code'")
        publish_events= _one("SELECT COUNT(*) FROM bot_events WHERE event_type='publish'")
        error_events  = _one("SELECT COUNT(*) FROM bot_events WHERE event_type='error'")

        cur = conn.execute(
            "SELECT deck_class, COUNT(*) cnt FROM generated_decks WHERE deck_class IS NOT NULL GROUP BY deck_class ORDER BY cnt DESC LIMIT 5"
        )
        top_classes = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

        cur = conn.execute(
            "SELECT deck_mode, COUNT(*) cnt FROM generated_decks WHERE deck_mode IS NOT NULL GROUP BY deck_mode ORDER BY cnt DESC"
        )
        top_modes = [{"mode": r[0], "count": r[1]} for r in cur.fetchall()]

        cur = conn.execute(
            "SELECT source, COUNT(*) cnt FROM generated_decks GROUP BY source ORDER BY cnt DESC"
        )
        by_source = [{"source": r[0] or "web", "count": r[1]} for r in cur.fetchall()]

    return {
        "total_decks":    total_decks,
        "today_decks":    today_decks,
        "week_decks":     week_decks,
        "total_users":    total_users,
        "total_events":   total_events,
        "deck_events":    deck_events,
        "publish_events": publish_events,
        "error_events":   error_events,
        "top_classes":    top_classes,
        "top_modes":      top_modes,
        "by_source":      by_source,
    }


def get_all_generated_decks(
    page: int = 1,
    per_page: int = 30,
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """Постраничный список колод для таблицы в панели администратора."""
    allowed = {"created_at", "cost", "deck_class", "deck_mode", "id"}
    if sort_by not in allowed:
        sort_by = "created_at"
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    clauses: List[str] = []
    params: List[Any] = []
    if deck_class:
        clauses.append("deck_class = ?")
        params.append(deck_class)
    if deck_mode:
        clauses.append("deck_mode = ?")
        params.append(deck_mode)
    if source:
        clauses.append("COALESCE(source,'web') = ?")
        params.append(source)
    if search:
        clauses.append("(deck_code LIKE ? OR COALESCE(deck_name,'') LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM generated_decks {where}", params).fetchone()[0]
        offset = (page - 1) * per_page
        cur = conn.execute(
            f"""SELECT id, deck_code, deck_name, cost, deck_class, deck_mode, source, created_at, filename
                FROM generated_decks {where}
                ORDER BY {sort_by} {direction}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        )
        items = [
            {
                "id":         r["id"],
                "deck_code":  r["deck_code"],
                "deck_name":  r["deck_name"] or None,
                "cost":       r["cost"],
                "deck_class": r["deck_class"] or None,
                "deck_mode":  r["deck_mode"] or None,
                "source":     r["source"] or "web",
                "created_at": r["created_at"],
                "filename":   r["filename"],
            }
            for r in cur.fetchall()
        ]
    return {
        "items":    items,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    max(1, (total + per_page - 1) // per_page),
    }


def get_decks_by_day(days: int = 30) -> List[Dict[str, Any]]:
    """Количество новых колод по дням."""
    with _get_conn() as conn:
        cur = conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as cnt
               FROM generated_decks
               WHERE created_at >= datetime('now', ?)
               GROUP BY DATE(created_at) ORDER BY day ASC""",
            (f"-{days} days",),
        )
        return [{"day": r["day"], "count": r["cnt"]} for r in cur.fetchall()]


def get_class_distribution() -> List[Dict[str, Any]]:
    """Распределение колод по классам."""
    with _get_conn() as conn:
        cur = conn.execute(
            """SELECT COALESCE(deck_class,'Неизвестно') as cls, COUNT(*) cnt
               FROM generated_decks GROUP BY deck_class ORDER BY cnt DESC"""
        )
        return [{"name": r["cls"], "count": r["cnt"]} for r in cur.fetchall()]


def get_mode_distribution_web() -> List[Dict[str, Any]]:
    """Распределение колод по режимам игры."""
    with _get_conn() as conn:
        cur = conn.execute(
            """SELECT COALESCE(deck_mode,'Неизвестно') as mode, COUNT(*) cnt
               FROM generated_decks GROUP BY deck_mode ORDER BY cnt DESC"""
        )
        return [{"mode": r["mode"], "count": r["cnt"]} for r in cur.fetchall()]


def get_cost_distribution_web() -> List[Dict[str, Any]]:
    """Гистограмма стоимости колод (пыль)."""
    with _get_conn() as conn:
        cur = conn.execute(
            """SELECT
                CASE
                    WHEN cost IS NULL OR cost = 0 THEN 'Бесплатно'
                    WHEN cost <= 2000  THEN '1–2000'
                    WHEN cost <= 5000  THEN '2001–5000'
                    WHEN cost <= 10000 THEN '5001–10000'
                    WHEN cost <= 20000 THEN '10001–20000'
                    ELSE '20000+'
                END as bucket,
                COUNT(*) cnt
               FROM generated_decks
               GROUP BY bucket
               ORDER BY MIN(COALESCE(cost,-1)) ASC"""
        )
        return [{"bucket": r["bucket"], "count": r["cnt"]} for r in cur.fetchall()]


def get_all_bot_users(
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Постраничный список пользователей бота с количеством колод."""
    clauses: List[str] = []
    params: List[Any] = []
    if search:
        clauses.append("(u.username LIKE ? OR u.first_name LIKE ? OR CAST(u.user_id AS TEXT) LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM bot_users u {where}", params
        ).fetchone()[0]
        offset = (page - 1) * per_page
        cur = conn.execute(
            f"""SELECT u.user_id, u.username, u.first_name, u.first_seen, u.last_seen,
                       COUNT(d.id) as deck_count,
                       MAX(d.created_at) as last_deck_at
                FROM bot_users u
                LEFT JOIN generated_decks d ON d.user_id = u.user_id
                {where}
                GROUP BY u.user_id
                ORDER BY deck_count DESC, u.last_seen DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        )
        items = [
            {
                "user_id":     r["user_id"],
                "username":    r["username"] or None,
                "first_name":  r["first_name"] or None,
                "first_seen":  r["first_seen"],
                "last_seen":   r["last_seen"],
                "deck_count":  r["deck_count"] or 0,
                "last_deck_at": r["last_deck_at"] or None,
            }
            for r in cur.fetchall()
        ]
    return {
        "items":    items,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    max(1, (total + per_page - 1) // per_page),
    }


def get_user_activity(user_id: int) -> Dict[str, Any]:
    """Полная история активности пользователя: его колоды и агрегаты."""
    with _get_conn() as conn:
        # Профиль
        row = conn.execute(
            "SELECT user_id, username, first_name, first_seen, last_seen FROM bot_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {}
        profile = {
            "user_id":    row["user_id"],
            "username":   row["username"] or None,
            "first_name": row["first_name"] or None,
            "first_seen": row["first_seen"],
            "last_seen":  row["last_seen"],
        }

        # Все колоды пользователя
        cur = conn.execute(
            """SELECT id, deck_code, deck_name, cost, deck_class, deck_mode,
                      source, created_at, filename
               FROM generated_decks
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        decks = [
            {
                "id":         r["id"],
                "deck_code":  r["deck_code"],
                "deck_name":  r["deck_name"] or None,
                "cost":       r["cost"],
                "deck_class": r["deck_class"] or None,
                "deck_mode":  r["deck_mode"] or None,
                "source":     r["source"] or "web",
                "created_at": r["created_at"],
                "filename":   r["filename"],
            }
            for r in cur.fetchall()
        ]

        # Агрегаты
        total_decks = len(decks)
        avg_cost = round(sum(d["cost"] or 0 for d in decks) / total_decks) if total_decks else 0
        fav_class = None
        fav_mode  = None
        if decks:
            from collections import Counter
            classes = [d["deck_class"] for d in decks if d["deck_class"]]
            modes   = [d["deck_mode"]  for d in decks if d["deck_mode"]]
            if classes:
                fav_class = Counter(classes).most_common(1)[0][0]
            if modes:
                fav_mode  = Counter(modes).most_common(1)[0][0]

    return {
        "profile":     profile,
        "decks":       decks,
        "total_decks": total_decks,
        "avg_cost":    avg_cost,
        "fav_class":   fav_class,
        "fav_mode":    fav_mode,
    }


def get_web_db_schema() -> List[Dict[str, Any]]:
    """Метаданные таблиц и колонок базы данных."""
    with _get_conn() as conn:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        result = []
        for tbl in tables:
            cols = [
                {
                    "cid":     r["cid"],
                    "name":    r["name"],
                    "type":    r["type"],
                    "notnull": bool(r["notnull"]),
                    "pk":      bool(r["pk"]),
                }
                for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()
            ]
            row_count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            result.append({"table": tbl, "columns": cols, "row_count": row_count})
    return result


def get_bot_stats_for_dashboard(days: int = 7) -> Dict[str, Any]:
    """Агрегаты для дашборда: по типам событий, по дням, по чатам."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:10] + "T00:00:00"
    with _get_conn() as conn:
        cur = conn.execute(
            """
            SELECT event_type, chat_type, DATE(created_at) as day, COUNT(*) as cnt
            FROM bot_events
            WHERE created_at >= ?
            GROUP BY event_type, chat_type, DATE(created_at)
            """,
            (since,),
        )
        rows = cur.fetchall()
    by_type: Dict[str, int] = {}
    by_day: Dict[str, Dict[str, int]] = {}
    by_chat: Dict[str, int] = {}
    for r in rows:
        t, chat, day, cnt = r["event_type"], r["chat_type"] or "unknown", r["day"], r["cnt"]
        by_type[t] = by_type.get(t, 0) + cnt
        by_chat[chat] = by_chat.get(chat, 0) + cnt
        if day not in by_day:
            by_day[day] = {}
        by_day[day][t] = by_day[day].get(t, 0) + cnt
    total = sum(by_type.values())
    return {
        "total_events": total,
        "by_type": by_type,
        "by_chat_type": by_chat,
        "by_day": by_day,
        "days": days,
    }


def get_deck_detail(deck_id: int) -> Optional[Dict[str, Any]]:
    """
    Полная информация о конкретной колоде для консольного лог-вида:
    - метаданные колоды
    - список dbf_id карт
    - профиль пользователя (если user_id есть)
    - события бота в окне ±10 мин от создания колоды
    - другие колоды того же пользователя (последние 10)
    """
    with _get_conn() as conn:
        deck_row = conn.execute(
            "SELECT * FROM generated_decks WHERE id = ?", (deck_id,)
        ).fetchone()
        if deck_row is None:
            return None
        deck = dict(deck_row)

        # Карты колоды
        cards = [r["dbf_id"] for r in conn.execute(
            "SELECT dbf_id FROM deck_cards WHERE generated_deck_id = ? ORDER BY dbf_id",
            (deck_id,),
        ).fetchall()]

        # Пользователь
        user = None
        if deck.get("user_id"):
            u = conn.execute(
                "SELECT * FROM bot_users WHERE user_id = ?", (deck["user_id"],)
            ).fetchone()
            if u:
                user = dict(u)

        # События ±10 мин от создания
        nearby_events: List[Dict] = []
        if deck.get("created_at"):
            rows = conn.execute(
                """
                SELECT id, event_type, chat_type, payload, created_at
                FROM bot_events
                WHERE created_at BETWEEN datetime(?, '-10 minutes')
                                     AND datetime(?, '+10 minutes')
                ORDER BY created_at
                """,
                (deck["created_at"], deck["created_at"]),
            ).fetchall()
            for r in rows:
                ev = dict(r)
                if ev.get("payload"):
                    try:
                        ev["payload"] = json.loads(ev["payload"])
                    except Exception:
                        pass
                nearby_events.append(ev)

        # Другие колоды того же пользователя (последние 10, кроме текущей)
        sibling_decks: List[Dict] = []
        if deck.get("user_id"):
            rows = conn.execute(
                """
                SELECT id, deck_code, deck_name, deck_class, deck_mode, cost, created_at
                FROM generated_decks
                WHERE user_id = ? AND id <> ?
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (deck["user_id"], deck_id),
            ).fetchall()
            sibling_decks = [dict(r) for r in rows]

    return {
        "deck": deck,
        "cards": cards,
        "user": user,
        "nearby_events": nearby_events,
        "sibling_decks": sibling_decks,
    }


def get_load_stats() -> Dict[str, Any]:
    """
    Статистика нагрузки и безопасности для страницы мониторинга:
    - события по часам за 24 ч (с разбивкой на ошибки/успех)
    - кол-во событий и ошибок за последний час
    - процент ошибок за 24 ч
    - топ сообщений об ошибках
    - паттерны флуда: окна по 5 мин за последний час
    - авто-детектированные предупреждения безопасности
    """
    with _get_conn() as conn:
        # 1. Почасовая активность за 24 ч
        hourly_rows = conn.execute(
            """
            SELECT strftime('%H', datetime(created_at)) AS hr,
                   event_type, COUNT(*) AS cnt
            FROM bot_events
            WHERE created_at >= datetime('now', '-24 hours')
            GROUP BY hr, event_type
            ORDER BY hr
            """
        ).fetchall()

        # 2. Последний час: всего / ошибок
        last_hour = conn.execute(
            "SELECT COUNT(*) FROM bot_events WHERE created_at >= datetime('now', '-1 hour')"
        ).fetchone()[0]
        last_hour_errors = conn.execute(
            "SELECT COUNT(*) FROM bot_events WHERE event_type='error' "
            "AND created_at >= datetime('now', '-1 hour')"
        ).fetchone()[0]

        # 3. За 24 ч: всего / ошибок
        last_24h = conn.execute(
            "SELECT COUNT(*) FROM bot_events WHERE created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        last_24h_errors = conn.execute(
            "SELECT COUNT(*) FROM bot_events WHERE event_type='error' "
            "AND created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]

        # 4. Топ ошибок — сгруппированы по тексту
        top_errors = conn.execute(
            """
            SELECT payload, COUNT(*) AS cnt, MAX(created_at) AS last_seen
            FROM bot_events
            WHERE event_type='error'
            GROUP BY payload
            ORDER BY cnt DESC
            LIMIT 15
            """
        ).fetchall()

        # 5. Паттерн флуда: события по 5-минутным окнам за последние 2 ч
        flood_windows = conn.execute(
            """
            SELECT strftime('%Y-%m-%d %H:', datetime(created_at)) ||
                   printf('%02d', (CAST(strftime('%M', datetime(created_at)) AS INTEGER)/5)*5)
                   AS window5,
                   COUNT(*) AS cnt,
                   SUM(CASE WHEN event_type='error' THEN 1 ELSE 0 END) AS err_cnt
            FROM bot_events
            WHERE created_at >= datetime('now', '-2 hours')
            GROUP BY window5
            ORDER BY window5 DESC
            LIMIT 24
            """
        ).fetchall()

        # 6. Источники: Telegram vs web
        source_rows = conn.execute(
            "SELECT source, COUNT(*) FROM generated_decks GROUP BY source"
        ).fetchall()

        # 7. Пользователи с высокой частотой ошибок (в generated_decks — proxy)
        # Ищем deck_code с ошибками в событиях за 24 ч
        repeat_errors = conn.execute(
            """
            SELECT payload, COUNT(*) AS cnt
            FROM bot_events
            WHERE event_type='error'
              AND created_at >= datetime('now', '-24 hours')
            GROUP BY payload
            HAVING cnt >= 5
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()

    # Формируем почасовой массив 00–23
    hourly: Dict[str, Dict[str, int]] = {}
    for row in hourly_rows:
        h = row["hr"]
        if h not in hourly:
            hourly[h] = {}
        hourly[h][row["event_type"]] = row["cnt"]

    hours_list = []
    for h in [f"{i:02d}" for i in range(24)]:
        bucket = hourly.get(h, {})
        total_h = sum(bucket.values())
        err_h = bucket.get("error", 0)
        hours_list.append({
            "hour": h + ":00",
            "total": total_h,
            "errors": err_h,
            "ok": total_h - err_h,
        })

    # Топ ошибок с парсингом JSON-payload
    top_err_parsed = []
    for row in top_errors:
        try:
            pl = json.loads(row["payload"]) if row["payload"] else {}
            msg = pl.get("error") or pl.get("message") or pl.get("detail") or str(pl)
            ctx = pl.get("context") or pl.get("cmd") or "—"
        except Exception:
            msg = str(row["payload"])[:120]
            ctx = "—"
        top_err_parsed.append({
            "message": msg,
            "context": ctx,
            "count": row["cnt"],
            "last_seen": row["last_seen"],
        })

    # Окна флуда — последние 24 (обращаем порядок → старые→новые)
    flood_list = [
        {"window": dict(r)["window5"], "total": dict(r)["cnt"], "errors": dict(r)["err_cnt"]}
        for r in reversed(flood_windows)
    ]
    max_window = max((f["total"] for f in flood_list), default=0)

    # Авто-предупреждения
    alerts = []
    error_rate_1h = round(100 * last_hour_errors / last_hour, 1) if last_hour else 0
    error_rate_24h = round(100 * last_24h_errors / last_24h, 1) if last_24h else 0

    if error_rate_1h >= 20:
        alerts.append({
            "level": "critical",
            "title": "Высокий процент ошибок",
            "detail": f"{error_rate_1h}% событий за последний час — ошибки. "
                      "Возможна атака некорректными кодами колод или проблема в боте.",
        })
    elif error_rate_1h >= 10:
        alerts.append({
            "level": "warning",
            "title": "Повышенный процент ошибок",
            "detail": f"{error_rate_1h}% событий за последний час — ошибки.",
        })

    if last_hour > 300:
        alerts.append({
            "level": "warning",
            "title": "Высокая нагрузка",
            "detail": f"{last_hour} событий за последний час — выше нормального уровня. "
                      "Проверьте на флуд или автоматические запросы.",
        })

    if max_window > 80:
        alerts.append({
            "level": "critical",
            "title": "Пик флуда (5 мин окно)",
            "detail": f"Пиковое окно: {max_window} событий за 5 мин. "
                      "Возможен флуд или DoS-подобная нагрузка.",
        })

    # Повторяющиеся ошибки: один тип ошибки > 20 раз за 24ч
    for row in repeat_errors:
        try:
            pl = json.loads(row["payload"]) if row["payload"] else {}
            msg = pl.get("error") or pl.get("message") or "неизвестная ошибка"
        except Exception:
            msg = str(row["payload"])[:80]
        alerts.append({
            "level": "warning",
            "title": f"Повторяющаяся ошибка × {row['cnt']}",
            "detail": f'"{msg}" — встречается {row["cnt"]} раз за 24 ч. '
                      "Возможна массовая отправка невалидных кодов.",
        })

    if not alerts:
        alerts.append({
            "level": "ok",
            "title": "Всё в порядке",
            "detail": "Подозрительной активности за последние 24 ч не обнаружено.",
        })

    sources = {dict(r)["source"]: dict(r)["COUNT(*)"] for r in source_rows}

    return {
        "summary": {
            "last_hour_events": last_hour,
            "last_hour_errors": last_hour_errors,
            "error_rate_1h": error_rate_1h,
            "last_24h_events": last_24h,
            "last_24h_errors": last_24h_errors,
            "error_rate_24h": error_rate_24h,
            "max_5min_window": max_window,
        },
        "hourly": hours_list,
        "top_errors": top_err_parsed,
        "flood_windows": flood_list,
        "sources": sources,
        "alerts": alerts,
    }


# ─── Publish Logs ──────────────────────────────────────────────────────────────

def add_publish_log(
    deck_name: Optional[str],
    deck_class: Optional[str],
    deck_mode: Optional[str],
    deck_code: Optional[str],
    telegram_sent: bool,
    wordpress_posted: bool,
    error: Optional[str] = None,
) -> None:
    """Сохраняет запись о попытке публикации колоды в канал/WordPress."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO publish_logs
               (deck_name, deck_class, deck_mode, deck_code, telegram_sent, wordpress_posted, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (deck_name or "").strip() or None,
                (deck_class or "").strip() or None,
                (deck_mode or "").strip() or None,
                (deck_code or "").strip() or None,
                1 if telegram_sent else 0,
                1 if wordpress_posted else 0,
                (error or "").strip() or None,
                now,
            ),
        )


def get_publish_logs(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Возвращает список записей публикаций, новые первыми."""
    with _get_conn() as conn:
        cur = conn.execute(
            """SELECT id, deck_name, deck_class, deck_mode, deck_code,
                      telegram_sent, wordpress_posted, error, created_at
               FROM publish_logs
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "deck_name": r["deck_name"],
            "deck_class": r["deck_class"],
            "deck_mode": r["deck_mode"],
            "deck_code": r["deck_code"],
            "telegram_sent": bool(r["telegram_sent"]),
            "wordpress_posted": bool(r["wordpress_posted"]),
            "error": r["error"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_publish_logs_count() -> int:
    """Общее число записей публикаций."""
    with _get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM publish_logs")
        row = cur.fetchone()
    return row[0] if row else 0


# ─── Archetypes (translations) ────────────────────────────────────────────────

def get_all_archetypes(
    search: Optional[str] = None,
    sort_by: str = "name_en",
    sort_dir: str = "asc",
) -> List[Dict[str, Any]]:
    """Returns list of all archetypes as dicts."""
    allowed_sort = {"name_en", "name_ru", "created_at", "updated_at", "id"}
    if sort_by not in allowed_sort:
        sort_by = "name_en"
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    clauses: List[str] = []
    params: List[Any] = []
    if search:
        clauses.append("(name_en LIKE ? OR name_ru LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _get_conn() as conn:
        cur = conn.execute(
            f"SELECT id, name_en, name_ru, created_at, updated_at FROM archetypes {where} ORDER BY {sort_by} {direction}",
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "name_en": r["name_en"],
            "name_ru": r["name_ru"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def add_archetype(name_en: str, name_ru: str) -> int:
    """Add new archetype. Returns id. Raises ValueError if name_en already exists."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM archetypes WHERE name_en = ?", (name_en,)
        ).fetchone()
        if existing:
            raise ValueError(f"Archetype '{name_en}' already exists")
        cur = conn.execute(
            "INSERT INTO archetypes (name_en, name_ru, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name_en, name_ru, now, now),
        )
        return cur.lastrowid


def update_archetype(arch_id: int, name_en: str, name_ru: str) -> bool:
    """Update archetype by id. Returns True if found and updated."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        # Check for unique name_en conflict (another row with same name_en)
        existing = conn.execute(
            "SELECT id FROM archetypes WHERE name_en = ? AND id != ?", (name_en, arch_id)
        ).fetchone()
        if existing:
            raise ValueError(f"Archetype '{name_en}' already exists")
        cur = conn.execute(
            "UPDATE archetypes SET name_en = ?, name_ru = ?, updated_at = ? WHERE id = ?",
            (name_en, name_ru, now, arch_id),
        )
        return cur.rowcount > 0


def delete_archetype(arch_id: int) -> bool:
    """Delete archetype by id. Returns True if found and deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM archetypes WHERE id = ?", (arch_id,))
        return cur.rowcount > 0


def import_archetypes_from_dict(data: dict) -> tuple:
    """Import archetypes from {eng: rus} dict. Returns (added, updated) counts.
    Uses INSERT OR REPLACE logic."""
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    updated = 0
    with _get_conn() as conn:
        for eng, rus in data.items():
            eng = eng.strip()
            rus = rus.strip()
            if not eng or not rus:
                continue
            existing = conn.execute(
                "SELECT id, name_ru FROM archetypes WHERE name_en = ?", (eng,)
            ).fetchone()
            if existing:
                if existing["name_ru"] != rus:
                    conn.execute(
                        "UPDATE archetypes SET name_ru = ?, updated_at = ? WHERE id = ?",
                        (rus, now, existing["id"]),
                    )
                    updated += 1
            else:
                conn.execute(
                    "INSERT INTO archetypes (name_en, name_ru, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (eng, rus, now, now),
                )
                added += 1
    return (added, updated)
