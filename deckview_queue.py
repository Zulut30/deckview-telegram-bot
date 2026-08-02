"""Compatibility alias for :mod:`deckview.workers.queue`."""
import sys
from deckview.workers import queue as _implementation
sys.modules[__name__] = _implementation
