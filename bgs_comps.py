"""Compatibility alias for :mod:`deckview.integrations.battlegrounds_stats`."""

import sys

from deckview.integrations import battlegrounds_stats as _implementation

sys.modules[__name__] = _implementation
