"""Compatibility alias for :mod:`deckview.infrastructure.render_cache`."""
import sys
from deckview.infrastructure import render_cache as _implementation
sys.modules[__name__] = _implementation
