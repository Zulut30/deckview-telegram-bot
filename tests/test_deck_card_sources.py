import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from image_creator import deck_card_sources
from image_creator.deck_card_sources import hydrate_deck_cards_sync
from framework import grequests_downloader


class DeckCardSourcesTest(unittest.TestCase):
    def setUp(self):
        with deck_card_sources._cache_lock:
            deck_card_sources._card_cache.clear()
            deck_card_sources._existing_slug_index = None

    def test_kolodahs_overrides_mana_and_supplies_card_id(self):
        cards = [{"id": 123, "slug": "123-test", "manaCost": 99}]
        with (
            patch(
                "image_creator.deck_card_sources._load_disk_cache",
                return_value={},
            ),
            patch(
                "image_creator.deck_card_sources._store_disk_cache",
            ),
            patch(
                "image_creator.deck_card_sources.get_kolodahs_card",
                return_value={"card_id": "/TEST_123", "mana": 4},
            ),
        ):
            hydrated = hydrate_deck_cards_sync(cards)
        self.assertEqual(hydrated[0]["manaCost"], 4)
        self.assertEqual(hydrated[0]["cardId"], "TEST_123")
        self.assertEqual(hydrated[0]["deckviewManaSource"], "kolodahs")
        self.assertEqual(hydrated[0]["deckviewImageSource"], "arena")

    def test_kolodahs_can_fully_hydrate_locally_decoded_card(self):
        cards = [{"id": 123, "dbfId": 123}]
        source = {
            "card_id": "TEST_123",
            "name": "Тестовая карта",
            "mana": 4,
            "rarity": "EPIC",
            "collectible": True,
            "player_class": "MAGE",
            "type": "HERO",
            "image_url": "https://kolodahs.ru/cards/ruru/TEST_123.png",
        }
        with (
            patch("image_creator.deck_card_sources._load_disk_cache", return_value={123: source}),
            patch.object(deck_card_sources, "_existing_slug", return_value="123-test-card"),
        ):
            hydrated = hydrate_deck_cards_sync(cards)

        card = hydrated[0]
        self.assertEqual(card["slug"], "123-test-card")
        self.assertEqual(card["name"], "Тестовая карта")
        self.assertEqual(card["manaCost"], 4)
        self.assertEqual(card["rarityId"], 4)
        self.assertEqual(card["deckviewPlayerClass"], "MAGE")
        self.assertEqual(card["deckviewCardType"], "HERO")

    def test_hsjson_identity_wins_when_kolodahs_dbf_mapping_is_wrong(self):
        cards = [{"id": 119705, "dbfId": 119705}]
        wrong_source = {
            "card_id": "TIME_609",
            "name": "Командир следопытов Сильвана",
            "mana": 3,
            "rarity": "LEGENDARY",
            "collectible": True,
            "player_class": "HUNTER",
            "type": "MINION",
            "image_url": "https://kolodahs.ru/cards/ruru/TIME_609.png",
        }
        canonical = {
            "cardId": "TIME_609t1",
            "name": "Капитан следопытов Аллерия",
            "manaCost": 3,
            "rarity": None,
            "collectible": False,
            "type": "MINION",
            "image": (
                "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/"
                "TIME_609t1.png"
            ),
        }
        with (
            patch(
                "image_creator.deck_card_sources.get_snapshot_cards",
                return_value={119705: wrong_source},
            ),
            patch(
                "image_creator.deck_card_sources.get_loaded_card_by_dbfid",
                return_value=canonical,
            ),
            patch.object(
                deck_card_sources,
                "_existing_slug",
                return_value="119705-time-609t1",
            ),
        ):
            hydrated = hydrate_deck_cards_sync(cards)

        card = hydrated[0]
        self.assertEqual(card["cardId"], "TIME_609t1")
        self.assertEqual(card["slug"], "119705-time-609t1")
        self.assertEqual(card["name"], "Капитан следопытов Аллерия")
        self.assertEqual(card["image"], canonical["image"])
        self.assertFalse(card["collectible"])
        self.assertEqual(
            card["deckviewMetadataFallback"],
            "hsjson-card-id-mismatch",
        )

    def test_existing_slug_never_reuses_sideboard_copy_for_main_deck(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            cards.mkdir()
            (cards / "40324-freezing-potion-side.arena-v1").write_text(
                "arena\n", encoding="utf-8"
            )
            (cards / "40324-freezing-potion.arena-v1").write_text(
                "arena\n", encoding="utf-8"
            )
            with patch.object(deck_card_sources, "_PROJECT_ROOT", root):
                slug = deck_card_sources._existing_slug(40324, "UNG_018")

        self.assertEqual(slug, "40324-freezing-potion")

    def test_existing_slug_ignores_sideboard_when_it_is_the_only_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            cards.mkdir()
            (cards / "192-ice-block-side.arena-v1").write_text(
                "arena\n", encoding="utf-8"
            )
            with patch.object(deck_card_sources, "_PROJECT_ROOT", root):
                slug = deck_card_sources._existing_slug(192, "EX1_295")

        self.assertEqual(slug, "192-ex1-295")

    def test_shared_cache_avoids_network_fetch(self):
        cards = [{"id": 123, "slug": "123-test", "manaCost": 99}]
        with (
            patch(
                "image_creator.deck_card_sources._load_disk_cache",
                return_value={123: {"card_id": "TEST_123", "mana": 4}},
            ),
            patch(
                "image_creator.deck_card_sources.get_kolodahs_card",
            ) as fetch,
        ):
            hydrated = hydrate_deck_cards_sync(cards)

        fetch.assert_not_called()
        self.assertEqual(hydrated[0]["manaCost"], 4)

    def test_snapshot_precedes_sqlite_and_network_fallbacks(self):
        cards = [{"id": 123, "dbfId": 123}]
        source = {
            "card_id": "SNAPSHOT_123",
            "name": "Карта из snapshot",
            "mana": 5,
            "rarity": "RARE",
            "collectible": True,
            "player_class": "PRIEST",
            "type": "SPELL",
        }
        with (
            patch(
                "image_creator.deck_card_sources.get_snapshot_cards",
                return_value={123: source},
            ),
            patch(
                "image_creator.deck_card_sources._load_disk_cache",
            ) as disk_cache,
            patch(
                "image_creator.deck_card_sources.get_kolodahs_card",
            ) as fetch,
            patch.object(
                deck_card_sources,
                "_existing_slug",
                return_value="123-snapshot-card",
            ),
        ):
            hydrated = hydrate_deck_cards_sync(cards)

        disk_cache.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(hydrated[0]["manaCost"], 5)
        self.assertEqual(hydrated[0]["cardId"], "SNAPSHOT_123")
        self.assertEqual(hydrated[0]["slug"], "123-snapshot-card")

    def test_arena_image_is_converted_and_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = f"{directory}/"
            image_path = Path(directory) / "123-test.png"
            marker_path = Path(directory) / "123-test.arena-v2"
            source = Image.new("RGB", (100, 150), "blue")
            source_path = Path(directory) / "source.webp"
            source.save(source_path, "WEBP")
            with (
                patch.object(grequests_downloader, "FOLDER", folder),
                patch(
                "deckview.integrations.manacost_api.get_card_image",
                    return_value=source_path.read_bytes(),
                ),
            ):
                ok = grequests_downloader._download_from_arena(
                    {"slug": "123-test", "cardId": "TEST_123"}
                )
            self.assertTrue(ok)
            self.assertTrue(image_path.is_file())
            self.assertTrue(marker_path.is_file())
            with Image.open(image_path) as result:
                self.assertEqual(result.size, (100, 150))

    def test_legacy_arena_marker_does_not_make_card_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = f"{directory}/"
            (Path(directory) / "123-test.png").write_bytes(b"image" * 30)
            (Path(directory) / "123-test.arena-v1").write_text(
                "arena.hs-manacost.ru\n",
                encoding="utf-8",
            )

            with patch.object(grequests_downloader, "FOLDER", folder):
                self.assertFalse(
                    grequests_downloader._has_arena_cached_photo("123-test")
                )

    def test_arena_placeholder_cache_is_replaced_by_hsjson_art(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = f"{directory}/"
            image_path = Path(directory) / "95650-core-icc-836.png"
            Image.new("RGB", (512, 776), "white").save(image_path)
            legacy_marker = Path(directory) / "95650-core-icc-836.arena-v1"
            legacy_marker.write_text("arena.hs-manacost.ru\n", encoding="utf-8")

            replacement = Path(directory) / "replacement.png"
            Image.new("RGB", (512, 776), "blue").save(replacement)
            response = SimpleNamespace(
                status_code=200,
                content=replacement.read_bytes(),
                headers={"Content-Type": "image/png"},
            )
            session = MagicMock()
            session.get.return_value = response
            card = {
                "id": 95650,
                "dbfId": 95650,
                "cardId": "CORE_ICC_836",
                "slug": "95650-core-icc-836",
                "name": "Дыхание Синдрагосы",
                "image": "https://art.hearthstonejson.test/CORE_ICC_836.png",
            }

            with (
                patch.object(grequests_downloader, "FOLDER", folder),
                patch(
                    "framework.grequests_downloader._use_local_arena_photo",
                    return_value=False,
                ),
                patch(
                    "deckview.integrations.manacost_api.get_card_image",
                    side_effect=RuntimeError("Arena placeholder"),
                ),
                patch(
                    "framework.grequests_downloader.get_http_session",
                    return_value=session,
                ),
            ):
                grequests_downloader.GRequestsDownloader().process_cards([card])

            self.assertFalse(legacy_marker.exists())
            with Image.open(image_path) as rendered:
                self.assertEqual(rendered.getpixel((0, 0)), (0, 0, 255, 255))

    def test_arena_image_prefers_prewarmed_dbf_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = f"{directory}/"
            source = Image.new("RGB", (100, 150), "blue")
            source_path = Path(directory) / "source.webp"
            source.save(source_path, "WEBP")

            with (
                patch.object(grequests_downloader, "FOLDER", folder),
                patch(
                    "framework.grequests_downloader._use_local_arena_photo",
                    return_value=False,
                ),
                patch(
                "deckview.integrations.manacost_api.get_card_image",
                    return_value=source_path.read_bytes(),
                ) as get_card_image,
            ):
                ok = grequests_downloader._download_from_arena(
                    {
                        "slug": "123-test",
                        "cardId": "TEST_123",
                        "dbfId": 123,
                    }
                )

            self.assertTrue(ok)
            get_card_image.assert_called_once_with("123", "full")

    def test_local_arena_image_bypasses_http_and_reencoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            arena = root / "arena-card-images"
            cards.mkdir()
            arena.mkdir()
            source_path = (
                arena / "TEST_123-full-blizzard-card_img_v6_blizzard.webp"
            )
            Image.new("RGB", (100, 150), "blue").save(source_path, "WEBP")
            source_bytes = source_path.read_bytes()

            with (
                patch.object(grequests_downloader, "FOLDER", f"{cards}/"),
                patch.dict(
                    "os.environ",
                    {"DECKVIEW_ARENA_CARD_IMAGE_DIR": str(arena)},
                ),
                patch(
                "deckview.integrations.manacost_api.get_card_image",
                    side_effect=AssertionError("HTTP must not be used"),
                ) as get_card_image,
            ):
                grequests_downloader._reset_local_arena_image_index()
                ok = grequests_downloader._download_from_arena(
                    {"slug": "123-test", "cardId": "TEST_123"}
                )

            self.assertTrue(ok)
            get_card_image.assert_not_called()
            self.assertTrue((cards / "123-test.png").is_symlink())
            self.assertEqual((cards / "123-test.png").read_bytes(), source_bytes)
            self.assertIn(
                "TEST_123-full-blizzard-card_img_v6_blizzard.webp",
                (cards / "123-test.arena-v2").read_text(encoding="utf-8"),
            )

    def test_local_arena_image_uses_dbf_prewarm_for_generated_card(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            arena = root / "arena-card-images"
            cards.mkdir()
            arena.mkdir()
            source_path = arena / "123-full-blizzard-card_img_v6_blizzard.webp"
            Image.new("RGB", (100, 150), "purple").save(source_path, "WEBP")

            with (
                patch.object(grequests_downloader, "FOLDER", f"{cards}/"),
                patch.dict(
                    "os.environ",
                    {"DECKVIEW_ARENA_CARD_IMAGE_DIR": str(arena)},
                ),
                patch(
                "deckview.integrations.manacost_api.get_card_image",
                    side_effect=AssertionError("HTTP must not be used"),
                ) as get_card_image,
            ):
                grequests_downloader._reset_local_arena_image_index()
                ok = grequests_downloader._download_from_arena(
                    {
                        "slug": "123-generated-side",
                        "cardId": "TEST_123t",
                        "dbfId": 123,
                    }
                )

            self.assertTrue(ok)
            get_card_image.assert_not_called()
            self.assertEqual(
                (cards / "123-generated-side.png").resolve(),
                source_path.resolve(),
            )
            with Image.open(cards / "123-generated-side.png") as linked_image:
                linked_image.load()
                self.assertEqual(linked_image.size, (100, 150))

    def test_unreadable_local_arena_image_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            arena = Path(directory)
            source_path = arena / "TEST_123-full-blizzard-card_img_v6_blizzard.webp"
            Image.new("RGB", (100, 150), "purple").save(source_path, "WEBP")

            with (
                patch.dict(
                    "os.environ",
                    {"DECKVIEW_ARENA_CARD_IMAGE_DIR": str(arena)},
                ),
                patch(
                    "framework.grequests_downloader.os.access",
                    return_value=False,
                ),
            ):
                grequests_downloader._reset_local_arena_image_index()
                result = grequests_downloader._resolve_local_arena_image(
                    "TEST_123"
                )

            self.assertIsNone(result)

    def test_local_arena_image_prefers_latest_visual_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            arena = Path(directory)
            old_blizzard = arena / "TEST_123-full-blizzard-card_img_v4.webp"
            current_fallback = arena / "TEST_123-full-fallback-card_img_v6.webp"
            Image.new("RGB", (100, 150), "blue").save(old_blizzard, "WEBP")
            Image.new("RGB", (100, 150), "red").save(current_fallback, "WEBP")

            with patch.dict(
                "os.environ",
                {"DECKVIEW_ARENA_CARD_IMAGE_DIR": str(arena)},
            ):
                grequests_downloader._reset_local_arena_image_index()
                result = grequests_downloader._resolve_local_arena_image(
                    "TEST_123"
                )

            self.assertEqual(result, current_fallback)

    def test_local_arena_image_replaces_stale_cached_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards = root / "cards"
            arena = root / "arena-card-images"
            cards.mkdir()
            arena.mkdir()
            cached = cards / "123-test.png"
            cached.write_bytes(b"old-image" * 20)
            (cards / "123-test.arena-v1").write_text(
                "arena.hs-manacost.ru\nlocal=TEST_123-full-card_img_v4.webp\n",
                encoding="utf-8",
            )
            current = arena / "TEST_123-full-blizzard-card_img_v6_blizzard.webp"
            Image.new("RGB", (100, 150), "red").save(current, "WEBP")

            with (
                patch.object(grequests_downloader, "FOLDER", f"{cards}/"),
                patch.dict(
                    "os.environ",
                    {"DECKVIEW_ARENA_CARD_IMAGE_DIR": str(arena)},
                ),
            ):
                grequests_downloader._reset_local_arena_image_index()
                ok = grequests_downloader._use_local_arena_photo(
                    {"slug": "123-test", "cardId": "TEST_123"}
                )

            self.assertTrue(ok)
            self.assertTrue(cached.is_symlink())
            self.assertEqual(cached.resolve(), current.resolve())

    def test_arena_batch_downloads_each_slug_once(self):
        cards = [
            {"slug": "one", "cardId": "CARD_1"},
            {"slug": "one", "cardId": "CARD_1"},
            {"slug": "two", "cardId": "CARD_2"},
            {"slug": "", "cardId": "CARD_3"},
        ]
        with patch(
            "framework.grequests_downloader._download_from_arena",
            return_value=True,
        ) as download:
            successful = grequests_downloader._download_arena_batch(cards)
        self.assertEqual(successful, {"one", "two"})
        self.assertEqual(download.call_count, 2)

    def test_collectible_hero_prefers_local_hearthstonejson_render(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = f"{directory}/"
            image_path = Path(directory) / "125467-deathwing-worldbreaker.png"
            hsjson_marker = Path(directory) / "125467-deathwing-worldbreaker.hsjson-v1"
            arena_marker = Path(directory) / "125467-deathwing-worldbreaker.arena-v2"
            arena_marker.write_text("arena\n", encoding="utf-8")
            Image.new("RGBA", (404, 558), "orange").save(image_path)

            full_render = Image.new("RGBA", (512, 776), "red")
            source_path = Path(directory) / "full.png"
            full_render.save(source_path)
            card = {
                "id": 125467,
                "slug": "125467-deathwing-worldbreaker",
                "cardId": "CATA_190h",
                "cardTypeId": 3,
            }

            def download_full_render(card_id, slug):
                image_path.write_bytes(source_path.read_bytes())
                return True

            with (
                patch.object(grequests_downloader, "FOLDER", folder),
                patch(
                    "framework.grequests_downloader.download_from_hearthstonejson",
                    side_effect=download_full_render,
                ),
            ):
                ok = grequests_downloader._download_hero_render(card)

            self.assertTrue(ok)
            self.assertTrue(hsjson_marker.is_file())
            self.assertFalse(arena_marker.exists())
            with Image.open(image_path) as result:
                self.assertEqual(result.size, (512, 776))


if __name__ == "__main__":
    unittest.main()
