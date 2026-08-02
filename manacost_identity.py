"""Compatibility alias for :mod:`deckview.integrations.manacost_identity`."""

import sys

from deckview.integrations import manacost_identity as _implementation

sys.modules[__name__] = _implementation
