PYTHON ?= .venv/bin/python

.PHONY: test reno verify bot web worker

test:
	$(PYTHON) -m unittest discover -v

reno:
	$(PYTHON) scripts/render_regression_decks.py --output-dir artifacts/reno-regression

verify: test reno

bot:
	$(PYTHON) main.py

web:
	$(PYTHON) web_app.py

worker:
	$(PYTHON) -m rq worker deckview
