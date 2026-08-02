"""Compatibility alias for :mod:`deckview.integrations.manacost_api`."""

import sys

from deckview.integrations import manacost_api as _implementation

sys.modules[__name__] = _implementation
