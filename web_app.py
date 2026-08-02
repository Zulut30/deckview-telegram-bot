"""Compatibility entrypoint for the packaged Flask application."""
import os
import sys
from deckview.web import application as _implementation
if __name__ == "__main__":
    debug = "--debug" in sys.argv or os.getenv("FLASK_DEBUG", "").lower() in {"1", "true"}
    _implementation.app.run(
        host=_implementation.WEB_HOST,
        port=_implementation.WEB_PORT,
        debug=debug,
    )
else:
    sys.modules[__name__] = _implementation
