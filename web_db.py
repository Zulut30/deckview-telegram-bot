"""Compatibility alias for :mod:`deckview.repositories.web`."""
import sys
from deckview.repositories import web as _implementation
sys.modules[__name__] = _implementation
