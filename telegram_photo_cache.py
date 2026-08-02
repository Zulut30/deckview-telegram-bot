"""Compatibility alias for :mod:`deckview.infrastructure.telegram_photo_cache`."""
import sys
from deckview.infrastructure import telegram_photo_cache as _implementation
sys.modules[__name__] = _implementation
