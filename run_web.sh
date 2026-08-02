#!/usr/bin/env bash
# Запуск веб-приложения на сайте (production).
# Перед первым запуском: poetry install
# Настройки в .env: WEB_HOST (0.0.0.0), WEB_PORT (5000), WEB_DATABASE_PATH, WEB_CACHE_MAX_AGE_HOURS

cd "$(dirname "$0")"
[ -f .env ] && set -a && source .env && set +a
export FLASK_APP=web_app:app
export DECKVIEW_WEB_PRELOAD_CARDS="${DECKVIEW_WEB_PRELOAD_CARDS:-1}"
BIND="${WEB_HOST:-0.0.0.0}:${WEB_PORT:-5000}"

# Таймаут воркера (сек): генерация колоды может занимать до 1–2 минут
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
WORKERS="${WEB_WORKERS:-2}"

if command -v poetry &>/dev/null; then
    poetry run gunicorn --preload -w "$WORKERS" -b "$BIND" --timeout "$GUNICORN_TIMEOUT" --access-logfile - --error-logfile - web_app:app
else
    gunicorn --preload -w "$WORKERS" -b "$BIND" --timeout "$GUNICORN_TIMEOUT" --access-logfile - --error-logfile - web_app:app
fi
