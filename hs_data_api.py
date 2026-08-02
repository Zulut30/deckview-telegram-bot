"""Compatibility alias for :mod:`deckview.integrations.hs_data_api`."""
import sys
from deckview.integrations import hs_data_api as _implementation
sys.modules[__name__] = _implementation
