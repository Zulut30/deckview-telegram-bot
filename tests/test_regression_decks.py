import unittest

from scripts.render_regression_decks import RENO_DECKS, validate_deck


class RenoRegressionFixturesTest(unittest.TestCase):
    def test_reno_fixtures_cover_30_and_40_singleton_decks(self):
        counts = [validate_deck(deck) for deck in RENO_DECKS]

        self.assertEqual([item["main_cards"] for item in counts], [30, 40])
        self.assertEqual([item["unique_cards"] for item in counts], [30, 40])


if __name__ == "__main__":
    unittest.main()
