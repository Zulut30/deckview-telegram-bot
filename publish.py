"""Compatibility alias for :mod:`deckview.bot.publishing`."""
import sys
from deckview.bot import publishing as _implementation
sys.modules[__name__] = _implementation
