"""Compatibility alias for :mod:`deckview.integrations.hsguru_fetch`."""
import sys
from deckview.integrations import hsguru_fetch as _implementation
sys.modules[__name__] = _implementation
