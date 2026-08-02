"""Compatibility alias for :mod:`deckview.services.health_checks`."""

import sys

from deckview.services import health_checks as _implementation

sys.modules[__name__] = _implementation
