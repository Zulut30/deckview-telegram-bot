import unittest
from unittest.mock import AsyncMock, patch

from image_creator import deck_retriever


class DeckRetrieverCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with deck_retriever._deck_cache_lock:
            deck_retriever._deck_cache.clear()

    async def test_cached_deck_skips_second_api_call_and_is_isolated(self):
        response = {
            "cards": [{"id": 123, "slug": "test-card"}],
            "class": {"id": 8},
            "cardCount": 1,
        }
        api = unittest.mock.Mock()
        api.get_from_code = AsyncMock(return_value=response)

        with (
            patch.object(deck_retriever, "get_blizzard_api", return_value=api),
            patch.object(deck_retriever, "_DECK_CACHE_TTL_SECONDS", 1800),
            patch.object(deck_retriever, "_get_disk_cached_deck", return_value=None),
            patch.object(deck_retriever, "_put_disk_cached_deck"),
        ):
            first = await deck_retriever.retrieve_deck("AA-test")
            first[0]["cards"][0]["slug"] = "changed-by-caller"
            second = await deck_retriever.retrieve_deck("AA-test")

        api.get_from_code.assert_awaited_once()
        self.assertEqual(second[0]["cards"][0]["slug"], "test-card")

    async def test_disabled_cache_calls_api_each_time(self):
        response = {
            "cards": [{"id": 123, "slug": "test-card"}],
            "class": {"id": 8},
            "cardCount": 1,
        }
        api = unittest.mock.Mock()
        api.get_from_code = AsyncMock(return_value=response)

        with (
            patch.object(deck_retriever, "get_blizzard_api", return_value=api),
            patch.object(deck_retriever, "_DECK_CACHE_TTL_SECONDS", 0),
        ):
            await deck_retriever.retrieve_deck("AA-test")
            await deck_retriever.retrieve_deck("AA-test")

        self.assertEqual(api.get_from_code.await_count, 2)

    async def test_shared_cache_skips_api_call(self):
        cached = (
            {
                "cards": [{"id": 123, "slug": "test-card"}],
                "class": {"id": 8},
                "cardCount": 1,
            },
            8,
            [],
        )
        api = unittest.mock.Mock()
        api.get_from_code = AsyncMock()

        with (
            patch.object(deck_retriever, "get_blizzard_api", return_value=api),
            patch.object(deck_retriever, "_DECK_CACHE_TTL_SECONDS", 1800),
            patch.object(
                deck_retriever,
                "_get_disk_cached_deck",
                return_value=cached,
            ),
        ):
            result = await deck_retriever.retrieve_deck("AA-shared")

        api.get_from_code.assert_not_called()
        self.assertEqual(result[1], 8)

    async def test_local_deckstring_path_skips_blizzard(self):
        code = (
            "AAECAa0GDqn1BsP/BvKDB4OKB6iWB4KYB/ypB4CqB4SqB4utB+SyB+ey"
            "B4O/B8nHBwjwnwSg+wb3gQeFhgedrQeixAeyxQeW/AcAAA=="
        )

        def hydrate(cards):
            for card in cards:
                dbf_id = card["dbfId"]
                card.update(
                    {
                        "slug": f"{dbf_id}-test",
                        "name": f"Card {dbf_id}",
                        "manaCost": 1,
                        "cardId": f"TEST_{dbf_id}",
                    }
                )
            return cards

        api = unittest.mock.Mock()
        api.get_from_code = AsyncMock()
        decoded_cards, _heroes, _format, _sideboard = (
            deck_retriever._decode_deckstring(code)
        )
        with (
            patch.object(deck_retriever, "hydrate_deck_cards_sync", side_effect=hydrate),
            patch.object(
                deck_retriever,
                "get_standard_dbf_ids",
                return_value={dbf_id for dbf_id, _copies in decoded_cards},
            ),
            patch.object(deck_retriever, "get_blizzard_api", return_value=api),
            patch.object(deck_retriever, "_get_disk_cached_deck", return_value=None),
            patch.object(deck_retriever, "_put_disk_cached_deck"),
        ):
            response, deck_class, sideboard = await deck_retriever.retrieve_deck(code)

        api.get_from_code.assert_not_called()
        self.assertEqual(deck_class, 6)
        self.assertEqual(response["format"], "standard")
        self.assertEqual(response["class"]["slug"], "priest")
        self.assertEqual(len(response["cards"]), 30)
        self.assertEqual(sideboard, [])

    def test_preloaded_hsjson_completes_missing_card_without_network(self):
        cards = [
            {
                "id": 123,
                "dbfId": 123,
                "manaCost": 7,
                "deckviewSideboard": True,
            }
        ]
        fallback = {
            "id": 123,
            "dbfId": 123,
            "slug": "123-generated-card",
            "name": "Generated Card",
            "manaCost": 1,
            "cardId": "TEST_123t",
            "image": "https://example.test/card.png",
        }

        with patch.object(
            deck_retriever,
            "get_loaded_card_by_dbfid",
            return_value=fallback,
        ) as get_loaded:
            result = deck_retriever._fill_missing_from_preloaded_hsjson(cards)

        get_loaded.assert_called_once_with(123)
        self.assertEqual(result[0]["slug"], "123-generated-card")
        self.assertEqual(result[0]["cardId"], "TEST_123t")
        # Authoritative Kolodahs mana must never be overwritten by fallback.
        self.assertEqual(result[0]["manaCost"], 7)
        self.assertEqual(
            result[0]["deckviewMetadataFallback"],
            "hsjson-memory",
        )

    def test_deckstring_decoder_preserves_quantities_and_hero(self):
        code = (
            "AAECAR8IlegD5e8D25EE57kEltQEmNQEl+8E4qQFEOrpA/DsA/T2A5T8"
            "A8OABPaPBKmfBIPIBL/TBMHjBMzkBNDkBKeQBaiTBa6TBY+kBQA="
        )
        cards, heroes, deck_format, sideboard = deck_retriever._decode_deckstring(code)
        self.assertEqual(heroes, [31])
        self.assertEqual(deck_format, 2)
        self.assertEqual(sum(quantity for _, quantity in cards), 40)
        self.assertEqual(len(cards), 24)
        self.assertEqual(sideboard, [])

    def test_rotated_card_overrides_stale_standard_deckstring_header(self):
        code = (
            "AAECAR8Kx6QGr8EGscEG9t0GiuIG4uMGyeUGquoGw4MH25cHCqmfBMufBs7A"
            "BovcBqfcBp/dBpXiBuHjBq3rBqSxBwABA/WzBsekBvezBsekBu7eBsekBgAA"
        )
        cards, _heroes, deck_format, _sideboard = (
            deck_retriever._decode_deckstring(code)
        )
        standard_ids = {dbf_id for dbf_id, _copies in cards}
        standard_ids.remove(102983)

        with patch.object(
            deck_retriever,
            "get_standard_dbf_ids",
            return_value=standard_ids,
        ):
            resolved = deck_retriever._resolve_deck_format(
                deck_format,
                [dbf_id for dbf_id, _copies in cards],
            )

        self.assertEqual(deck_format, 2)
        self.assertEqual(resolved, "wild")


if __name__ == "__main__":
    unittest.main()
