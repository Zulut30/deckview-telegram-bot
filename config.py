"""Compatibility alias for :mod:`deckview.config`."""
import sys
from deckview import config as _implementation
sys.modules[__name__] = _implementation
