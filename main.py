"""Compatibility entrypoint for the packaged Deckview Telegram application."""

from __future__ import annotations

import sys

from deckview.bot import application as _application


if __name__ == "__main__":
    _application.run()
else:
    # Keep historical imports and test patching working while all production
    # code uses ``deckview.bot.application`` directly.
    sys.modules[__name__] = _application
