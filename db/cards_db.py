"""
Локальная SQLite-база карт (импорт из cards_ru.sql).
Используется для точного определения редкости и collectible-статуса карт по dbfId.
"""
import os
import sqlite3
import threading

_DB_PATH = os.path.join(os.path.dirname(__file__), "cards_local.db")

# Стоимость крафта по редкости (строка → пыль)
RARITY_COST = {
    "free":       0,
    "common":     40,
    "rare":       100,
    "epic":       400,
    "legendary":  1600,
}

# Наборы, карты из которых бесплатны для всех игроков
FREE_SETS = {"core", "path_of_arthas", "emerald_dream", "the_lost_city"}

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Возвращает thread-local соединение с БД."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


def get_card_by_dbf(dbf_id: int) -> dict | None:
    """Возвращает запись карты по dbfId или None если не найдена."""
    try:
        cur = _conn().execute(
            "SELECT card_id, rarity, collectible, set_name FROM cards_ru WHERE dbf = ?",
            (int(dbf_id),)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "card_id":    row["card_id"],
            "rarity":     row["rarity"],
            "collectible": row["collectible"],
            "set_name":   row["set_name"],
        }
    except Exception:
        return None


def get_card_cost(dbf_id: int) -> int | None:
    """
    Возвращает стоимость крафта карты в пыли по dbfId.
    None если карта не найдена в базе.
    """
    card = get_card_by_dbf(dbf_id)
    if card is None:
        return None

    # Некол. карты (collectible='0') — не крафтятся, стоят 0
    if str(card["collectible"]) == "0":
        return 0

    # Карты из бесплатных наборов
    set_norm = (card["set_name"] or "").strip().lower()
    if set_norm in FREE_SETS:
        return 0

    rarity_norm = (card["rarity"] or "").strip().lower()
    return RARITY_COST.get(rarity_norm, 0)
