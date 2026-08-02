from db.constants import COST_OF_CARDS
from db.cards_db import get_card_cost
from threader import to_thread


@to_thread
def get_cost_of_deck(cards):
    """
    Оценка стоимости колоды в пыли.

    Приоритет источников для каждой карты:
    1. Локальная SQLite (cards_local.db, импорт из cards_ru.sql) — ищем по dbfId.
       Если найдена → используем её rarity + collectible + set напрямую.
       Бесплатные наборы (FREE_SETS) определяются в db/cards_db.py.
    2. Blizzard API rarityId + HSJSON fallback (старая логика) — если карта
       не нашлась в локальной базе.
    """
    cost = 0

    for card in cards:
        qty = card.get("deckQuantity") or card.get("quantity") or 1
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            qty = 1

        # Пробуем dbfId: Blizzard API кладёт его в 'id' или 'dbfId'
        raw_dbf = card.get("dbfId") if card.get("dbfId") is not None else card.get("id")
        dbf_id = None
        if raw_dbf is not None:
            try:
                dbf_id = int(raw_dbf)
            except (TypeError, ValueError):
                pass

        # ── 1. Локальная база (cards_local.db) ───────────────────────────
        if dbf_id is not None:
            local_cost = get_card_cost(dbf_id)
            if local_cost is not None:
                cost += local_cost * qty
                continue

        # ── 2. Fallback: Blizzard API rarityId ───────────────────────────
        rarity_id = card.get("rarityId")

        # Некол. карты (collectible=0/False) → бесплатно
        raw_collectible = card.get("collectible")
        if raw_collectible is not None and not raw_collectible:
            cost += 0 * qty
            continue

        card_cost = COST_OF_CARDS.get(rarity_id, COST_OF_CARDS.get(None, 0))
        cost += card_cost * qty

    return cost
