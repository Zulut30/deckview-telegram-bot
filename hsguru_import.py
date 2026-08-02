"""Compatibility alias for :mod:`deckview.integrations.hsguru_import`."""
import sys
from deckview.integrations import hsguru_import as _implementation
sys.modules[__name__] = _implementation
