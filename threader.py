"""Compatibility alias for :mod:`deckview.infrastructure.async_tools`."""
import sys
from deckview.infrastructure import async_tools as _implementation
sys.modules[__name__] = _implementation
