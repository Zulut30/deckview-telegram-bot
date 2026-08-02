"""Compatibility alias for :mod:`deckview.web.dashboard`."""
import sys
from deckview.web import dashboard as _implementation
sys.modules[__name__] = _implementation
