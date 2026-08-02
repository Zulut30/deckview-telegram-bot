from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckview.repositories import web


class HsguruImportRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "deckview.db"
        self.path_patch = patch.object(web, "WEB_DATABASE_PATH", str(self.database))
        self.path_patch.start()
        web._db_initialized = False
        web.init_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        web._db_initialized = False
        self.tempdir.cleanup()

    def test_deck_code_exists_and_archetype_snapshot_upsert(self) -> None:
        self.assertFalse(web.deck_code_exists("AAECA-test"))
        web.add_generated(
            "AAECA-test",
            "Test deck",
            0,
            "test.jpg",
        )
        self.assertTrue(web.deck_code_exists("AAECA-test"))

        first = {
            "name_en": "Control Priest",
            "name_ru": "Контроль Жрец",
            "hero_class": "Жрец",
            "format": "Стандарт",
            "winrate": 51.2,
            "game_count": 100,
            "popularity": "2.5%",
            "snapshot_date": "2026-08-02",
        }
        self.assertEqual(1, web.upsert_archetype_stats([first]))
        self.assertEqual(
            1,
            web.upsert_archetype_stats(
                [{**first, "winrate": 52.0, "game_count": 120}]
            ),
        )
        with web._get_conn() as connection:
            rows = connection.execute(
                "SELECT winrate, game_count FROM archetype_stats"
            ).fetchall()
        self.assertEqual([(52.0, 120)], [tuple(row) for row in rows])


if __name__ == "__main__":
    unittest.main()
