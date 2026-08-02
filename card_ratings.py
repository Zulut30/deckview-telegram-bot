"""Compatibility alias for :mod:`deckview.repositories.card_ratings`."""

import sys

from deckview.repositories import card_ratings as _implementation

sys.modules[__name__] = _implementation
