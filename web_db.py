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

from config import WEB_CACHE_MAX_AGE_HOURS, WEB_DATABASE_PATH

# Guard: init_db() выполняется только один раз за процесс
_db_initialized = False


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
            WHERE deck_code = ? AND COALESCE(deck_name, '') = ? AND created_at >= ?
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
