# Развёртывание

Production состоит из трёх долгоживущих процессов:

- `deckview-bot.service` — Telegram polling/webhook orchestration;
- `deckview-web.service` — Gunicorn/Flask API и dashboard;
- `rq worker deckview` — тяжёлый рендер изображений.

Redis используется для очереди и дедупликации. Nginx завершает TLS, применяет rate limits и отдаёт готовые изображения.

## Подготовка

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Проверьте права на runtime-каталоги `cache/`, `cards/`, `static/generated/` и `user_assets/`. Они не входят в Git и должны быть доступны пользователю сервисов.

## Прогрев

В production рекомендуется включить:

```dotenv
DECKVIEW_QUEUE_ENABLED=1
DECKVIEW_WORKER_PRELOAD_CARDS=1
DECKVIEW_WEB_PRELOAD_CARDS=1
DECKVIEW_CARD_SNAPSHOT=1
DECKVIEW_RENDER_CACHE_WRITE=1
DECKVIEW_RENDER_CACHE_READ=1
```

Снимок каталога обновляется вне request path:

```bash
.venv/bin/python -m tools.refresh_card_catalog_snapshot
```

Для периодического обновления доступны `deploy/deckview-card-snapshot.service` и `.timer`.

## Проверка релиза

```bash
make test
make reno
systemctl is-active deckview-bot deckview-web deckview-worker
```

После рестарта выполните health-check и один холодный/тёплый render. Сравните stage timings и убедитесь, что тёплый запрос использует кеш.

## Откат

Код откатывается на предыдущий проверенный commit; runtime базы и пользовательские файлы не удаляются. Версии `DECKVIEW_RENDERER_VERSION`, `DECKVIEW_TEMPLATE_VERSION` и `DECKVIEW_CARD_DATA_VERSION` должны соответствовать откатываемому коду, иначе старые изображения намеренно не будут cache hit.

Пример Nginx-конфигурации находится в `deploy/nginx-blizzcore.conf`.
