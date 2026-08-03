import unittest
from unittest.mock import Mock, patch

from deckview.integrations import arena_stats as arena


class ManacostPublicApiTest(unittest.TestCase):
    def test_fetch_normalizes_class_statistics(self):
        response = Mock()
        response.json.return_value = {
            "data": [
                {
                    "classId": "demon-hunter",
                    "name": "Demon Hunter",
                    "metrics": {
                        "games": 1640,
                        "winratePercent": 56.2,
                        "sevenPlusWinsPercent": 18.4,
                    },
                },
                {
                    "classId": "rogue",
                    "name": "Rogue",
                    "metrics": {
                        "games": 218,
                        "winratePercent": 35.6,
                        "sevenPlusWinsPercent": 92,
                    },
                },
            ],
            "meta": {
                "entity": "classes",
                "updatedAt": "2026-07-30T12:00:00Z",
                "dataStatus": "fresh",
                "datasetVersion": "arena-classes-test",
            },
        }

        with (
            patch.object(arena, "MANACOST_PUBLIC_API_KEY", "test-key"),
            patch.object(arena, "get_http_session") as get_session,
        ):
            get_session.return_value.get.return_value = response
            result = arena._fetch_manacost_public_api()

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result["_source"], "manacost_public_api")
        self.assertEqual(
            result["stats"],
            [
                {
                    "playerClass": "Охотник на демонов",
                    "totalGames": 1640,
                    "_win_rate": 56.2,
                    "_source": "manacost_public_api",
                    "_pct_7plus": 18.4,
                },
                {
                    "playerClass": "Разбойник",
                    "totalGames": 218,
                    "_win_rate": 35.6,
                    "_source": "manacost_public_api",
                    "_pct_7plus": 0.92,
                },
            ],
        )

    def test_message_labels_manacost_source(self):
        text = arena.format_arena_message(
            {
                "_source": "manacost_public_api",
                "_period": "hsreplay",
                "stats": [
                    {
                        "playerClass": "Маг",
                        "totalGames": 100,
                        "_win_rate": 51.5,
                        "_pct_7plus": 12.0,
                    }
                ],
            },
            "hsreplay",
        )

        self.assertIn("arena.hs-manacost.ru", text)
        self.assertIn("51.5%", text)
        self.assertIn("12.0%", text)
        self.assertIn("7 и более победами", text)

    def test_missing_api_seven_plus_metric_is_enriched_from_hsreplay(self):
        response = Mock()
        response.json.return_value = {
            "data": [
                {
                    "classId": "mage",
                    "name": "Маг",
                    "metrics": {
                        "games": 868,
                        "winratePercent": 49.3,
                        "sevenPlusWinsPercent": None,
                    },
                }
            ],
            "meta": {
                "entity": "classes",
                "updatedAt": "2026-07-30T18:15:54Z",
                "dataStatus": "fresh",
                "datasetVersion": "arena-classes-null-seven-plus",
            },
        }

        with (
            patch.object(arena, "MANACOST_PUBLIC_API_KEY", "test-key"),
            patch.object(arena, "get_http_session") as get_session,
            patch.object(
                arena,
                "_fetch_hsreplay",
                return_value={
                    "stats": [
                        {
                            "playerClass": "Маг",
                            "totalGames": 864,
                            "_win_rate": 49.2,
                            "_pct_7plus": 6.83,
                        }
                    ]
                },
            ),
        ):
            get_session.return_value.get.return_value = response
            result = arena._fetch_manacost_public_api()

        self.assertEqual(result["stats"][0]["_pct_7plus"], 6.83)
        self.assertEqual(result["_seven_plus_source"], "hsreplay")
        text = arena.format_arena_message(result, "hsreplay")
        self.assertIn("7+:</b> 6.8%", text)
        self.assertIn("доля ранов с 7 и более победами", text)
        self.assertIn("arena.hs-manacost.ru + hsreplay.net", text)


if __name__ == "__main__":
    unittest.main()
