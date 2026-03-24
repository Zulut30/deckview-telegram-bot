"""
Модуль для работы с базой данных SQLite для хранения колод.
"""
import sqlite3
from datetime import datetime
from typing import List, Optional
from pathlib import Path


class DeckDatabase:
    """Класс для работы с базой данных колод."""
    
    def __init__(self, db_path: Path = Path("decks.db")):
        """
        Инициализация базы данных.
        
        Args:
            db_path: Путь к файлу базы данных SQLite
        """
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self) -> sqlite3.Connection:
        """Создает и возвращает соединение с базой данных."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
        return conn
    
    def init_db(self) -> None:
        """Создает таблицы, если они не существуют."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Таблица колод
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_code TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT,
                dust_cost INTEGER
            )
        """)
        
        # Таблица карт в колодах (для поиска)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deck_cards (
                deck_id INTEGER NOT NULL,
                card_dbf_id INTEGER NOT NULL,
                FOREIGN KEY (deck_id) REFERENCES decks(id) ON DELETE CASCADE,
                PRIMARY KEY (deck_id, card_dbf_id)
            )
        """)
        
        # Создаем индексы для быстрого поиска
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_deck_cards_dbf_id 
            ON deck_cards(card_dbf_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decks_code 
            ON decks(deck_code)
        """)

        # Таблица голосов за колоды (по message_id)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deck_votes (
                message_id INTEGER PRIMARY KEY,
                like_count INTEGER DEFAULT 0,
                dislike_count INTEGER DEFAULT 0
            )
        """)

        # Таблица голосов пользователей (чтобы не было повторных оценок)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deck_user_votes (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                PRIMARY KEY (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES deck_votes(message_id) ON DELETE CASCADE
            )
        """)
        
        conn.commit()
        conn.close()
        print(f"База данных инициализирована: {self.db_path}")
    
    def save_deck(self, deck_code: str, mode: str, dust_cost: int, 
                  card_dbf_ids: List[int]) -> bool:
        """
        Сохраняет колоду в базу данных.
        Если колода уже существует, обновляет timestamp.
        
        Args:
            deck_code: Код колоды (AAE...)
            mode: Режим игры (Стандартный/Вольный)
            dust_cost: Стоимость пыли
            card_dbf_ids: Список dbfId всех карт в колоде (включая сайдборды)
            
        Returns:
            True если успешно сохранено, False в случае ошибки
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Проверяем, существует ли колода
            cursor.execute("SELECT id FROM decks WHERE deck_code = ?", (deck_code,))
            existing = cursor.fetchone()
            
            if existing:
                # Обновляем timestamp существующей колоды
                deck_id = existing['id']
                cursor.execute("""
                    UPDATE decks 
                    SET created_at = CURRENT_TIMESTAMP, mode = ?, dust_cost = ?
                    WHERE id = ?
                """, (mode, dust_cost, deck_id))
                
                # Удаляем старые карты
                cursor.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
            else:
                # Создаем новую колоду
                cursor.execute("""
                    INSERT INTO decks (deck_code, mode, dust_cost)
                    VALUES (?, ?, ?)
                """, (deck_code, mode, dust_cost))
                deck_id = cursor.lastrowid
            
            # Добавляем карты
            for dbf_id in card_dbf_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO deck_cards (deck_id, card_dbf_id)
                    VALUES (?, ?)
                """, (deck_id, dbf_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            print(f"Ошибка при сохранении колоды: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def find_decks_containing_card(self, card_dbf_id: int, limit: int = 5) -> List[str]:
        """
        Находит колоды, содержащие указанную карту.
        
        Args:
            card_dbf_id: dbfId карты для поиска
            limit: Максимальное количество результатов
            
        Returns:
            Список кодов колод, отсортированных по дате создания (новые первыми)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT DISTINCT d.deck_code
                FROM decks d
                INNER JOIN deck_cards dc ON d.id = dc.deck_id
                WHERE dc.card_dbf_id = ?
                ORDER BY d.created_at DESC
                LIMIT ?
            """, (card_dbf_id, limit))
            
            results = cursor.fetchall()
            return [row['deck_code'] for row in results]
            
        except Exception as e:
            print(f"Ошибка при поиске колод: {e}")
            return []
        finally:
            conn.close()

    def get_vote_counts(self, message_id: int) -> dict:
        """Возвращает количество лайков/дизлайков для сообщения."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT like_count, dislike_count FROM deck_votes WHERE message_id = ?",
                (message_id,)
            )
            row = cursor.fetchone()
            if row:
                return {"like": row["like_count"], "dislike": row["dislike_count"]}
            return {"like": 0, "dislike": 0}
        finally:
            conn.close()

    def register_vote(self, message_id: int, user_id: int, vote_type: str) -> Optional[dict]:
        """
        Регистрирует голос пользователя за сообщение.
        Возвращает словарь с флагом already, либо None при ошибке.
        """
        if vote_type not in ("like", "dislike"):
            return None

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO deck_votes (message_id, like_count, dislike_count) VALUES (?, 0, 0)",
                (message_id,)
            )

            cursor.execute(
                "SELECT vote FROM deck_user_votes WHERE message_id = ? AND user_id = ?",
                (message_id, user_id)
            )
            existing = cursor.fetchone()
            if existing and existing["vote"] == vote_type:
                conn.commit()
                return {"already": True}

            if existing:
                prev_vote = existing["vote"]
                if prev_vote == "like":
                    cursor.execute(
                        "UPDATE deck_votes SET like_count = MAX(like_count - 1, 0) WHERE message_id = ?",
                        (message_id,)
                    )
                elif prev_vote == "dislike":
                    cursor.execute(
                        "UPDATE deck_votes SET dislike_count = MAX(dislike_count - 1, 0) WHERE message_id = ?",
                        (message_id,)
                    )
                cursor.execute(
                    "UPDATE deck_user_votes SET vote = ? WHERE message_id = ? AND user_id = ?",
                    (vote_type, message_id, user_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO deck_user_votes (message_id, user_id, vote) VALUES (?, ?, ?)",
                    (message_id, user_id, vote_type)
                )

            if vote_type == "like":
                cursor.execute(
                    "UPDATE deck_votes SET like_count = like_count + 1 WHERE message_id = ?",
                    (message_id,)
                )
            else:
                cursor.execute(
                    "UPDATE deck_votes SET dislike_count = dislike_count + 1 WHERE message_id = ?",
                    (message_id,)
                )

            conn.commit()
            return {"already": False}
        except Exception as e:
            print(f"Ошибка при сохранении голоса: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def get_statistics(self) -> dict:
        """
        Получает статистику по колодам.
        
        Returns:
            Словарь со статистикой
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Общее количество колод
            cursor.execute("SELECT COUNT(*) as total FROM decks")
            total_decks = cursor.fetchone()["total"]
            
            # Колоды за сегодня
            cursor.execute("""
                SELECT COUNT(*) as today FROM decks 
                WHERE DATE(created_at) = DATE('now')
            """)
            today_decks = cursor.fetchone()["today"]
            
            # Колоды за последние 7 дней
            cursor.execute("""
                SELECT COUNT(*) as week FROM decks 
                WHERE created_at >= datetime('now', '-7 days')
            """)
            week_decks = cursor.fetchone()["week"]
            
            # Общее количество голосов
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(like_count), 0) as total_likes,
                    COALESCE(SUM(dislike_count), 0) as total_dislikes
                FROM deck_votes
            """)
            votes = cursor.fetchone()
            total_likes = votes["total_likes"]
            total_dislikes = votes["total_dislikes"]
            
            # Топ режимов
            cursor.execute("""
                SELECT mode, COUNT(*) as cnt FROM decks 
                WHERE mode IS NOT NULL 
                GROUP BY mode ORDER BY cnt DESC LIMIT 3
            """)
            top_modes = [(row["mode"], row["cnt"]) for row in cursor.fetchall()]
            
            return {
                "total_decks": total_decks,
                "today_decks": today_decks,
                "week_decks": week_decks,
                "total_likes": total_likes,
                "total_dislikes": total_dislikes,
                "top_modes": top_modes
            }
            
        except Exception as e:
            print(f"Ошибка при получении статистики: {e}")
            return {
                "total_decks": 0,
                "today_decks": 0,
                "week_decks": 0,
                "total_likes": 0,
                "total_dislikes": 0,
                "top_modes": []
            }
        finally:
            conn.close()

    def get_all_decks(
        self,
        page: int = 1,
        per_page: int = 30,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
    ) -> dict:
        """
        Возвращает постраничный список колод для панели администратора.

        Returns:
            dict с ключами items (list), total, page, per_page, pages
        """
        allowed_sort = {"created_at", "dust_cost", "mode", "id"}
        if sort_by not in allowed_sort:
            sort_by = "created_at"
        sort_dir = "ASC" if sort_dir.upper() == "ASC" else "DESC"

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            where_clauses: list = []
            params: list = []
            if mode:
                where_clauses.append("mode = ?")
                params.append(mode)
            if search:
                where_clauses.append("deck_code LIKE ?")
                params.append(f"%{search}%")

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            cursor.execute(f"SELECT COUNT(*) as cnt FROM decks {where_sql}", params)
            total = cursor.fetchone()["cnt"]

            offset = (page - 1) * per_page
            cursor.execute(
                f"""
                SELECT id, deck_code, mode, dust_cost, created_at
                FROM decks {where_sql}
                ORDER BY {sort_by} {sort_dir}
                LIMIT ? OFFSET ?
                """,
                params + [per_page, offset],
            )
            rows = cursor.fetchall()
            items = [
                {
                    "id": r["id"],
                    "deck_code": r["deck_code"],
                    "mode": r["mode"],
                    "dust_cost": r["dust_cost"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": max(1, (total + per_page - 1) // per_page),
            }
        except Exception as e:
            print(f"Ошибка при получении списка колод: {e}")
            return {"items": [], "total": 0, "page": 1, "per_page": per_page, "pages": 1}
        finally:
            conn.close()

    def get_decks_per_day(self, days: int = 30) -> list:
        """Количество новых колод по дням за последние N дней."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT DATE(created_at) as day, COUNT(*) as cnt
                FROM decks
                WHERE created_at >= datetime('now', ?)
                GROUP BY DATE(created_at)
                ORDER BY day ASC
                """,
                (f"-{days} days",),
            )
            rows = cursor.fetchall()
            return [{"day": r["day"], "count": r["cnt"]} for r in rows]
        except Exception as e:
            print(f"Ошибка при получении статистики по дням: {e}")
            return []
        finally:
            conn.close()

    def get_mode_distribution(self) -> list:
        """Распределение колод по режимам игры."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT COALESCE(mode, 'Неизвестно') as mode, COUNT(*) as cnt
                FROM decks
                GROUP BY mode
                ORDER BY cnt DESC
                """
            )
            rows = cursor.fetchall()
            return [{"mode": r["mode"], "count": r["cnt"]} for r in rows]
        except Exception as e:
            print(f"Ошибка при получении распределения по режимам: {e}")
            return []
        finally:
            conn.close()

    def get_cost_distribution(self) -> list:
        """Распределение колод по стоимости пыли (по диапазонам)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    CASE
                        WHEN dust_cost IS NULL THEN 'Нет данных'
                        WHEN dust_cost = 0 THEN 'Бесплатно'
                        WHEN dust_cost <= 2000 THEN '1–2000'
                        WHEN dust_cost <= 5000 THEN '2001–5000'
                        WHEN dust_cost <= 10000 THEN '5001–10000'
                        WHEN dust_cost <= 20000 THEN '10001–20000'
                        ELSE '20000+'
                    END as bucket,
                    COUNT(*) as cnt
                FROM decks
                GROUP BY bucket
                ORDER BY MIN(COALESCE(dust_cost, -1)) ASC
                """
            )
            rows = cursor.fetchall()
            return [{"bucket": r["bucket"], "count": r["cnt"]} for r in rows]
        except Exception as e:
            print(f"Ошибка при получении распределения по стоимости: {e}")
            return []
        finally:
            conn.close()

    def get_top_voted_decks(self, limit: int = 10) -> list:
        """Топ колод по лайкам (через message_id)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT dv.message_id, dv.like_count, dv.dislike_count
                FROM deck_votes dv
                ORDER BY dv.like_count DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "message_id": r["message_id"],
                    "likes": r["like_count"],
                    "dislikes": r["dislike_count"],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"Ошибка при получении топ колод по голосам: {e}")
            return []
        finally:
            conn.close()

    def get_db_schema_info(self) -> list:
        """Метаданные таблиц и колонок для визуализации схемы БД."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r["name"] for r in cursor.fetchall()]
            result = []
            for table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [
                    {
                        "cid": r["cid"],
                        "name": r["name"],
                        "type": r["type"],
                        "notnull": bool(r["notnull"]),
                        "pk": bool(r["pk"]),
                    }
                    for r in cursor.fetchall()
                ]
                cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                row_count = cursor.fetchone()["cnt"]
                result.append({"table": table, "columns": cols, "row_count": row_count})
            return result
        except Exception as e:
            print(f"Ошибка при получении схемы БД: {e}")
            return []
        finally:
            conn.close()

    def get_last_deck(self) -> Optional[dict]:
        """
        Получает последнюю добавленную колоду.
        
        Returns:
            Словарь с данными колоды или None
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT deck_code, mode, dust_cost, created_at 
                FROM decks 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {
                    "deck_code": row["deck_code"],
                    "mode": row["mode"],
                    "dust_cost": row["dust_cost"],
                    "created_at": row["created_at"]
                }
            return None
        except Exception as e:
            print(f"Ошибка при получении последней колоды: {e}")
            return None
        finally:
            conn.close()


