"""Compatibility alias for :mod:`deckview.infrastructure.perf_telemetry`."""
import sys
from deckview.infrastructure import perf_telemetry as _implementation
sys.modules[__name__] = _implementation
