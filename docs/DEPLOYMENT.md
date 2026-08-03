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

Для периодического обновления доступны
`deploy/systemd/deckview-card-snapshot.service` и `.timer`.

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

## GitHub CI/CD

`.github/workflows/tests.yml` запускается на каждом pull request и push в
`main`: компилирует пакетные точки входа, выполняет все
unit/architecture/regression тесты и проверяет канонические package imports.
Ручной
параметр `render_regressions` дополнительно строит и сохраняет как Actions
artifact эталонные Reno-колоды на 30 и 40 карт.

Production deployment выполняется только вручную workflow
`Deploy production` через GitHub Environment `production`. Рекомендуется
включить required reviewers для этого Environment. Необходимые secrets:

| Secret | Значение |
|---|---|
| `DEPLOY_HOST` | hostname/IP VPS |
| `DEPLOY_USER` | непривилегированный SSH user |
| `DEPLOY_SSH_KEY` | отдельный deploy key |
| `DEPLOY_PORT` | SSH port, опционально (по умолчанию `22`) |
| `DEPLOY_BASE` | каталог релизов, обычно `/srv/deckview` |
| `DEPLOY_SERVICES` | systemd units через пробел |
| `DEPLOY_HEALTHCHECK_URL` | внутренний health endpoint |

Однократная подготовка сервера:

```bash
sudo install -d -o ubuntu -g ubuntu /srv/deckview/{releases,shared}
sudo cp /home/ubuntu/Deckview/.env /srv/deckview/shared/.env
sudo chmod 600 /srv/deckview/shared/.env
```

Systemd units должны использовать `/srv/deckview/current` как
`WorkingDirectory`, а исполняемые файлы — из
`/srv/deckview/current/.venv/bin/`. Deploy user получает узкое sudo-правило
только для перезапуска `deckview-bot`, `deckview-web` и `deckview-worker`.

Готовые units находятся в `deploy/systemd/`. Установите их один раз:

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deckview-bot deckview-web deckview-worker
sudo systemctl enable --now deckview-card-snapshot.timer
```

`deploy/github_deploy.sh` создаёт immutable checkout по полному SHA, подключает
shared runtime-каталоги, ставит зависимости, прогоняет тесты и оба Reno render,
после чего атомарно меняет symlink `current`. Если systemd restart или health
check завершается ошибкой, symlink возвращается на предыдущий релиз и сервисы
перезапускаются повторно.
