"""Compatibility alias for :mod:`deckview.services.archetype_service`."""
import sys
from deckview.services import archetype_service as _implementation
sys.modules[__name__] = _implementation
