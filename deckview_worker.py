"""Compatibility entrypoint for the packaged RQ worker."""
import sys
from deckview.workers import worker as _implementation
if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
