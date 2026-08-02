"""Compatibility alias for :mod:`deckview.integrations.hsguru_meta`."""
import sys
from deckview.integrations import hsguru_meta as _implementation
sys.modules[__name__] = _implementation
