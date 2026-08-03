import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from deckview.bot import application as main
from deckview.integrations import manacost_api


DECK_CODE = "AAECAR8EmacHmqcHm6cHxbEHDamfBKqfBK+SB4WVB86bB5inB7nAB7vAB97EB6zYB9faB9PbB9fbBwAA"


class ManacostApiTests(unittest.TestCase):
    def setUp(self):
        with manacost_api._lock:
            manacost_api._cache.clear()
            manacost_api._cache_errors.clear()
            manacost_api._cache_inflight.clear()

    def test_flexible_search_repairs_mixed_alphabet_query(self):
        card = {
            "id": "LOE_011",
            "dbfId": 2883,
            "name": {"ru": "Рено Джексон", "en": "Reno Jackson"},
            "formats": ["wild"],
        }
        with patch.object(
            manacost_api,
            "search_cards",
            side_effect=[[], [card]],
        ) as search:
            result = manacost_api.search_cards_flexible("ренo")
        self.assertEqual(result, [card])
        self.assertEqual(search.call_args_list[1].args[0], "рено")

    def test_flexible_search_keeps_only_exact_name_when_api_has_text_matches(self):
        fireball = {
            "id": "CORE_CS2_029",
            "dbfId": 69501,
            "name": {"ru": "Огненный шар", "en": "Fireball"},
            "formats": ["standard", "wild"],
        }
        antonidas = {
            "id": "CORE_EX1_559",
            "dbfId": 120426,
            "name": {
                "ru": "Верховный маг Антонидас",
                "en": "Archmage Antonidas",
            },
            "formats": ["standard", "wild"],
        }
        with patch.object(
            manacost_api,
            "search_cards",
            return_value=[antonidas, fireball],
        ):
            result = manacost_api.search_cards_flexible("огненный шар")
        self.assertEqual(result, [fireball])

    def test_flexible_search_keeps_non_collectible_card_with_manacost_image(self):
        local_only = {
            "id": 74694,
            "cardId": "LETL_237e",
            "name": "Апокалипсис",
            "text": "Получает критический урон.",
            "type": "ENCHANTMENT",
            "set": "LETTUCE",
            "collectible": False,
        }
        with (
            patch.object(manacost_api, "search_cards", return_value=[]),
            patch.object(
                manacost_api,
                "get_card",
                side_effect=manacost_api.ManacostAPIError(
                    "API вернул HTTP 404: Card was not found"
                ),
            ),
            patch.object(
                manacost_api,
                "_card_image_available",
                return_value=True,
            ),
            patch(
                "framework.hearthstonejson_api.find_cards_by_query",
                return_value=[local_only],
            ),
        ):
            result = manacost_api.search_cards_flexible("апокалипсис")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "LETL_237e")
        self.assertEqual(result[0]["dbfId"], 74694)
        self.assertEqual(result[0]["_metadataFallback"], "hearthstonejson")

    def test_flexible_search_repairs_wrong_keyboard_layout(self):
        card = {
            "id": "TOY_809",
            "dbfId": 103630,
            "name": {
                "ru": "Картонный голем",
                "en": "Cardboard Golem",
            },
            "formats": ["wild"],
        }
        with patch.object(
            manacost_api,
            "search_cards",
            side_effect=[[], [card]],
        ) as search:
            result = manacost_api.search_cards_flexible(
                "rfhnjyysq ujktv"
            )
        self.assertEqual(result, [card])
        self.assertEqual(
            search.call_args_list[1].args[0],
            "картонный голем",
        )

    def test_full_art_uses_vertical_wiki_gallery_original(self):
        metadata_response = MagicMock(
            status_code=200,
        )
        metadata_response.json.return_value = {
            "card": {
                "wiki": {
                    "gallery": [
                        {
                            "caption": "Test Card, full art",
                            "file_title": "File:Test_Card_full.jpg",
                            "file_url": "https://hearthstone.wiki.gg/images/Test_Card_full.jpg",
                        }
                    ]
                }
            }
        }
        art_response = MagicMock(
            status_code=200,
            content=b"vertical-art",
            headers={"Content-Type": "image/jpeg"},
        )
        session = MagicMock()
        session.get.side_effect = [metadata_response, art_response]
        with patch.object(manacost_api, "_http_session", return_value=session):
            result = manacost_api.get_card_full_art("TEST_FULL_ART")
        self.assertEqual(result, b"vertical-art")
        self.assertIn(
            "/api/constructed-cards/TEST_FULL_ART",
            session.get.call_args_list[0].args[0],
        )

    def test_full_art_falls_back_for_non_collectible_card(self):
        metadata_response = MagicMock(status_code=404)
        art_response = MagicMock(
            status_code=200,
            content=b"fallback-art",
            headers={"Content-Type": "image/jpeg"},
        )
        session = MagicMock()
        session.get.side_effect = [metadata_response, art_response]
        with patch.object(manacost_api, "_http_session", return_value=session):
            result = manacost_api.get_card_full_art("TB_Diablo4_Promo_Card2")
        self.assertEqual(result, b"fallback-art")
        self.assertIn(
            "art.hearthstonejson.com/v1/512x/TB_Diablo4_Promo_Card2.jpg",
            session.get.call_args_list[1].args[0],
        )

    def test_deckstring_decoder_returns_card_ids(self):
        card_ids = manacost_api._decode_deckstring(DECK_CODE)
        self.assertGreaterEqual(len(card_ids), 10)
        self.assertTrue(all(isinstance(value, int) for value in card_ids))

    def test_card_caption_contains_requested_fields(self):
        card = {
            "id": "TEST_001",
            "dbfId": 123,
            "name": {"ru": "Тестовая карта", "en": "Test Card"},
            "text": {"ru": "<b>Боевой клич:</b> делает что-то."},
            "flavor": {"ru": "Художественный текст."},
            "cost": 4,
            "attack": 3,
            "health": 5,
            "type": {"id": "MINION", "nameRu": "Существо"},
            "rarity": "LEGENDARY",
            "mechanics": ["BATTLECRY", "TRIGGER_VISUAL"],
        }
        caption = main._card_caption(card, likes=2, dislikes=1)
        self.assertNotIn("маны", caption)
        self.assertNotIn("⚔️", caption)
        self.assertNotIn("❤️", caption)
        self.assertIn("Описание", caption)
        self.assertIn("Художественный текст", caption)
        self.assertIn("<b>Механики:</b> Боевой клич", caption)
        self.assertNotIn("TRIGGER_VISUAL", caption)
        self.assertLessEqual(len(caption), 1024)

    def test_meta_contains_patch_links_and_deck_code(self):
        payload = {
            "format": "standard",
            "meta": {"period": {"patch": "36.0.3"}},
            "items": [
                {
                    "slug": "test-deck",
                    "classId": "mage",
                    "localizedName": "Тестовый архетип",
                    "metrics": {
                        "winratePercent": 52.4,
                        "popularityPercent": 4.2,
                        "games": 1234,
                    },
                    "links": {"web": "https://arena.hs-manacost.ru/standard/test"},
                }
            ],
        }
        text = main._format_manacost_meta(
            payload,
            {"test-deck": {"deckCode": DECK_CODE}},
            {"patch": "36.0.3"},
        )
        self.assertIn("36.0.3", text)
        self.assertIn('href="https://arena.hs-manacost.ru/', text)
        self.assertIn("<tg-emoji", text)
        self.assertIn("5438158414758831946", text)
        self.assertIn(DECK_CODE, text)

    def test_meta_is_ranked_by_winrate_not_popularity(self):
        payload = {
            "format": "standard",
            "meta": {"period": {"patch": "36.0.3"}},
            "items": [
                {
                    "slug": "popular-deck",
                    "localizedName": "Популярная колода",
                    "metrics": {
                        "winratePercent": 51.0,
                        "popularityPercent": 25.0,
                        "games": 25_000,
                    },
                },
                {
                    "slug": "winning-deck",
                    "localizedName": "Победная колода",
                    "metrics": {
                        "winratePercent": 58.0,
                        "popularityPercent": 2.0,
                        "games": 2_000,
                    },
                },
            ],
        }

        text = main._format_manacost_meta(payload, {}, {"patch": "36.0.3"})

        self.assertIn("Победная колода", text.splitlines()[3])
        self.assertLess(text.index("Победная колода"), text.index("Популярная колода"))

    def test_findwith_callbacks_fit_telegram_limit(self):
        deck = {
            "deck_id": "deck_3c421365f2d01fb702d4350ed9fb6568",
            "deck_name": "Фейс Охотник",
            "games": 171244,
            "winrate": 59.7,
        }
        keyboard = main._build_findwith_api_keyboard([deck], "abc123", 0)
        self.assertLessEqual(len(keyboard.inline_keyboard[0][0].callback_data), 64)

    def test_card_web_url_uses_direct_detail_route_and_format(self):
        standard_url = manacost_api.card_web_url(
            {"id": "CATA_111", "formats": ["standard", "wild"]}
        )
        wild_url = manacost_api.card_web_url(
            {"id": "TOY_809", "formats": ["wild"]}
        )
        self.assertEqual(
            standard_url,
            "https://arena.hs-manacost.ru/standard/cards/standard/CATA_111/",
        )
        self.assertEqual(
            wild_url,
            "https://arena.hs-manacost.ru/standard/cards/wild/TOY_809/",
        )

    def test_cache_singleflight_collapses_concurrent_loaders(self):
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return {"ok": True}

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    lambda _: manacost_api._cached(
                        "singleflight-test",
                        60,
                        loader,
                    ),
                    range(12),
                )
            )

        self.assertEqual(calls, 1)
        self.assertTrue(all(result == {"ok": True} for result in results))


class MetaLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main._META_VIEW_CACHE.clear()

    async def asyncTearDown(self):
        main._META_VIEW_CACHE.clear()

    async def test_meta_loader_fetches_full_source_before_winrate_ranking(self):
        payload = {
            "format": "standard",
            "meta": {"period": {"patch": "36.0.3"}},
            "items": [
                {
                    "slug": "popular-deck",
                    "localizedName": "Популярная колода",
                    "metrics": {
                        "winratePercent": 51.0,
                        "popularityPercent": 25.0,
                        "games": 25_000,
                    },
                },
                {
                    "slug": "winning-deck",
                    "localizedName": "Победная колода",
                    "metrics": {
                        "winratePercent": 58.0,
                        "popularityPercent": 2.0,
                        "games": 2_000,
                    },
                },
            ],
        }

        with (
            patch.object(main, "manacost_get_meta", return_value=payload) as get_meta,
            patch.object(
                main,
                "manacost_best_decks_by_archetype",
                return_value=({}, {"patch": "36.0.3"}),
            ),
        ):
            text, _keyboard = await main._load_manacost_meta(1)

        get_meta.assert_called_once_with(1, limit=main.META_SOURCE_LIMIT)
        self.assertLess(text.index("Победная колода"), text.index("Популярная колода"))


if __name__ == "__main__":
    unittest.main()
