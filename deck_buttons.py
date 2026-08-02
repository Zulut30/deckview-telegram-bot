"""Compatibility alias for :mod:`deckview.keyboards.deck_actions`."""

import sys

from deckview.keyboards import deck_actions as _implementation

sys.modules[__name__] = _implementation
