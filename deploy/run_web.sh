#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

export FLASK_APP=deckview.web.application:app
export DECKVIEW_WEB_PRELOAD_CARDS="${DECKVIEW_WEB_PRELOAD_CARDS:-1}"
bind="${WEB_HOST:-0.0.0.0}:${WEB_PORT:-5000}"
timeout="${GUNICORN_TIMEOUT:-120}"
workers="${WEB_WORKERS:-2}"

if [[ -x .venv/bin/gunicorn ]]; then
    runner=(.venv/bin/gunicorn)
elif command -v poetry >/dev/null 2>&1; then
    runner=(poetry run gunicorn)
else
    runner=(gunicorn)
fi

exec "${runner[@]}" --preload -w "$workers" -b "$bind" \
    --timeout "$timeout" --access-logfile - --error-logfile - \
    deckview.web.application:app
