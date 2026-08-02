"""Compatibility alias for :mod:`deckview.integrations.wordpress`."""
import sys
from deckview.integrations import wordpress as _implementation
sys.modules[__name__] = _implementation
