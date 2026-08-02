import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CardCatalogSnapshotTests(unittest.TestCase):
    def test_standard_catalog_fetches_all_cursor_pages(self):
        from image_creator import card_catalog_snapshot

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.cursors = []

            def get(self, _url, *, params, headers, timeout):
                self.cursors.append((params.get("cursor"), timeout))
                if params.get("cursor") is None:
                    return FakeResponse(
                        {
                            "data": [
                                {"id": "CARD_1", "dbfId": 101},
                                {"id": "CARD_2", "dbfId": 102},
                            ],
                            "pagination": {
                                "total": 3,
                                "hasMore": True,
                                "nextCursor": "page-2",
                            },
                            "meta": {"datasetVersion": "standard-v1"},
                        }
                    )
                return FakeResponse(
                    {
                        "data": [{"id": "CARD_3", "dbfId": 103}],
                        "pagination": {
                            "total": 3,
                            "hasMore": False,
                            "nextCursor": None,
                        },
                        "meta": {"datasetVersion": "standard-v1"},
                    }
                )

        session = FakeSession()
        result = card_catalog_snapshot.fetch_standard_dbf_ids(
            api_root="https://example.test/api/v1",
            api_key="secret",
            timeout=2.5,
            session=session,
        )

        self.assertEqual(result["dbf_ids"], {101, 102, 103})
        self.assertEqual(result["revision"], "standard-v1")
        self.assertEqual(result["source_total"], 3)
        self.assertEqual(session.cursors, [(None, 2.5), ("page-2", 2.5)])

    def test_bulk_lookup_loads_selected_cards_and_hot_reloads(self):
        from image_creator import card_catalog_snapshot

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "cards-current.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "revision": "first",
                        "standard_dbf_ids": [123],
                        "cards": {
                            "123": {"card_id": "TEST_123", "mana": 4},
                            "456": {"card_id": "TEST_456", "mana": 6},
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "DECKVIEW_CARD_SNAPSHOT": "1",
                    "DECKVIEW_CARD_SNAPSHOT_PATH": str(snapshot),
                },
            ):
                card_catalog_snapshot._reset_snapshot_cache()
                self.assertEqual(
                    card_catalog_snapshot.get_snapshot_cards([456, 999]),
                    {456: {"card_id": "TEST_456", "mana": 6}},
                )
                self.assertEqual(
                    card_catalog_snapshot.get_standard_dbf_ids(),
                    {123},
                )

                replacement = snapshot.with_suffix(".new")
                replacement.write_text(
                    json.dumps(
                        {
                            "schema": 1,
                            "revision": "second",
                            "standard_dbf_ids": [456],
                            "cards": {
                                "456": {"card_id": "TEST_456", "mana": 7}
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                os.replace(replacement, snapshot)

                self.assertEqual(
                    card_catalog_snapshot.get_snapshot_cards([456]),
                    {456: {"card_id": "TEST_456", "mana": 7}},
                )
                self.assertEqual(
                    card_catalog_snapshot.get_standard_dbf_ids(),
                    {456},
                )

    def test_disabled_or_corrupt_snapshot_fails_open(self):
        from image_creator import card_catalog_snapshot

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "cards-current.json"
            snapshot.write_text("not json", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "DECKVIEW_CARD_SNAPSHOT": "1",
                    "DECKVIEW_CARD_SNAPSHOT_PATH": str(snapshot),
                },
            ):
                card_catalog_snapshot._reset_snapshot_cache()
                self.assertEqual(
                    card_catalog_snapshot.get_snapshot_cards([123]),
                    {},
                )

    def test_refresh_fetches_all_pages_and_atomically_replaces_snapshot(self):
        from image_creator import card_catalog_snapshot

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.offsets = []

            def get(self, _url, *, params, timeout):
                self.offsets.append((params["offset"], timeout))
                if params["offset"] == 0:
                    cards = [
                        {
                            "id": 10,
                            "dbf": 123,
                            "card_id": "TEST_123",
                            "name": "Первая",
                            "mana": 4,
                            "description": "not persisted",
                        },
                        {
                            "dbf": 456,
                            "card_id": "TEST_456",
                            "name": "Вторая",
                            "mana": 6,
                        },
                        {
                            "id": 20,
                            "dbf": 123,
                            "card_id": "TEST_123_2",
                            "name": "Первая, другой класс",
                            "mana": 4,
                            "player_class": "WARRIOR",
                        },
                    ]
                    next_offset = 3
                else:
                    cards = [
                        {
                            "dbf": 789,
                            "card_id": "TEST_789",
                            "name": "Третья",
                            "mana": 8,
                        }
                    ]
                    next_offset = None
                return FakeResponse(
                    {
                        "success": True,
                        "result": {
                            "total": 4,
                            "cards": cards,
                            "next_offset": next_offset,
                        },
                    }
                )

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "cards-current.json"
            snapshot.write_text("old", encoding="utf-8")
            session = FakeSession()

            result = card_catalog_snapshot.refresh_snapshot(
                target_path=snapshot,
                api_root="https://example.test/api/v1",
                page_size=2,
                timeout=3.0,
                session=session,
            )

            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(result["card_count"], 3)
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["card_count"], 3)
            self.assertEqual(payload["cards"]["123"]["mana"], 4)
            self.assertNotIn("description", payload["cards"]["123"])
            self.assertEqual(payload["cards"]["123"]["card_id"], "TEST_123")
            self.assertEqual(session.offsets, [(0, 3.0), (3, 3.0)])
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

            with patch.dict(
                os.environ,
                {
                    "DECKVIEW_CARD_SNAPSHOT": "0",
                    "DECKVIEW_CARD_SNAPSHOT_PATH": str(snapshot),
                },
            ):
                card_catalog_snapshot._reset_snapshot_cache()
                self.assertEqual(
                    card_catalog_snapshot.get_snapshot_cards([123]),
                    {},
                )

    def test_refresh_keeps_previous_snapshot_when_catalog_is_incomplete(self):
        from image_creator import card_catalog_snapshot

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "success": True,
                    "result": {
                        "total": 2,
                        "cards": [
                            {
                                "id": 10,
                                "dbf": 123,
                                "card_id": "TEST_123",
                                "mana": 4,
                            }
                        ],
                        "next_offset": None,
                    },
                }

        class FakeSession:
            def get(self, _url, *, params, timeout):
                return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "cards-current.json"
            snapshot.write_text("previous", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "incomplete"):
                card_catalog_snapshot.refresh_snapshot(
                    target_path=snapshot,
                    api_root="https://example.test/api/v1",
                    session=FakeSession(),
                )

            self.assertEqual(snapshot.read_text(encoding="utf-8"), "previous")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
