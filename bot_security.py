"""Compatibility alias for :mod:`deckview.middlewares.flood_protection`."""

import sys

from deckview.middlewares import flood_protection as _implementation

sys.modules[__name__] = _implementation
