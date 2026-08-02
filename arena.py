"""Compatibility alias for :mod:`deckview.integrations.arena_stats`.

New code must import the packaged module directly.  The alias is kept while
legacy scripts and third-party imports migrate to the modular package.
"""

import sys

from deckview.integrations import arena_stats as _implementation

sys.modules[__name__] = _implementation
