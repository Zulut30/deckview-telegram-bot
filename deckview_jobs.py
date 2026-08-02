"""Compatibility alias for :mod:`deckview.workers.jobs`."""
import sys
from deckview.workers import jobs as _implementation
sys.modules[__name__] = _implementation
