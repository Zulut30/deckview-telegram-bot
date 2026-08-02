"""
Сохранение оценок карт (лайк/дизлайк) от пользователей во всех чатах.
Рейтинг карты — суммарные голоса по dbfId (один пользователь = один голос на карту, последний выбор сохраняется).
Защита от накрутки: кулдаун 60 сек — повторное изменение голоса по той же карте недоступно.
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Tuple

_RATINGS_DB = Path(__file__).resolve().parent / "cache" / "card_ratings.db"
RATING_COOLDOWN_SEC = 60

_ratings_initialized = False


@contextmanager
def _get_conn():
    _RATINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_RATINGS_DB, check_same_thread=False)
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


def init_ratings_db() -> None:
    """Создаёт таблицу оценок, если её ещё нет. Идемпотентна: повторный вызов — no-op."""
    global _ratings_initialized
    if _ratings_initialized:
        return
    _ratings_initialized = True
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS card_ratings (
                dbf_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                vote INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (dbf_id, user_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_ratings_dbf ON card_ratings(dbf_id)")


def add_rating(dbf_id: int, user_id: int, vote: int) -> Tuple[bool, str]:
    """
    Сохранить оценку: vote 1 = лайк, -1 = дизлайк.
    Возвращает (True, "") при успехе, (False, "сообщение") при кулдауне.
    """
    now = int(time.time())
    v = 1 if vote == 1 else -1
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT updated_at FROM card_ratings WHERE dbf_id = ? AND user_id = ?",
            (dbf_id, user_id),
        )
        row = cur.fetchone()
        if row and (now - row[0]) < RATING_COOLDOWN_SEC:
            return False, f"Подождите {RATING_COOLDOWN_SEC} сек. перед повторным голосованием."
        conn.execute(
            "INSERT INTO card_ratings (dbf_id, user_id, vote, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (dbf_id, user_id) DO UPDATE SET vote = excluded.vote, updated_at = excluded.updated_at",
            (dbf_id, user_id, v, now),
        )
    return True, ""


def get_rating(dbf_id: int) -> Tuple[int, int]:
    """Вернуть (лайки, дизлайки) для карты по dbfId."""
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) "
            "FROM card_ratings WHERE dbf_id = ?",
            (dbf_id,),
        )
        row = cur.fetchone()
    if not row or (row[0] is None and row[1] is None):
        return 0, 0
    return (row[0] or 0), (row[1] or 0)
