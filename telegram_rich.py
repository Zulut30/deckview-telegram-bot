"""Compatibility alias for :mod:`deckview.bot.rich`."""
import sys
from deckview.bot import rich as _implementation
sys.modules[__name__] = _implementation
