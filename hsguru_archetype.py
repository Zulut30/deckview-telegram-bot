"""Compatibility alias for :mod:`deckview.integrations.hsguru_archetype`."""
import sys
from deckview.integrations import hsguru_archetype as _implementation
sys.modules[__name__] = _implementation
