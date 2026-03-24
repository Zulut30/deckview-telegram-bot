# Hearthstone Deck Visualizer Bot

Telegram-бот и HTTP API для визуализации колод Hearthstone. Бот автоматически определяет коды колод в сообщениях, генерирует красивые изображения с картами и публикует колоды стримеров на сайт и в Telegram-канал.

**Сайт:** [hs-manacost.ru](https://hs-manacost.ru)
**Telegram-канал:** [@dcboom_hs](https://t.me/dcboom_hs)

---

## Содержание

- [Публичное API](#-публичное-api)
  - [Получить изображение колоды](#1-получить-изображение-колоды)
  - [Получить метаданные колоды](#2-получить-метаданные-колоды)
  - [Список переводов архетипов](#3-список-переводов-архетипов)
  - [Перевести название колоды](#4-перевести-название-колоды)
- [Установка и настройка](#установка-и-настройка)
- [Запуск](#запуск)
- [Структура проекта](#структура-проекта)
- [Команды бота](#команды-бота)

---

## 🌐 Публичное API

Публичное API доступно **без авторизации** по адресу сервера. Все публичные endpoints имеют префикс `/public/`.

Интерактивная документация Swagger UI доступна по адресу:
```
https://your-server/docs
```

---

### 1. Получить изображение колоды

Генерирует PNG-изображение колоды по её коду.

```
GET /public/render?deck=<код_колоды>
```

**Параметры:**

| Параметр | Тип    | Обязательный | Описание                                  |
|----------|--------|--------------|-------------------------------------------|
| `deck`   | string | ✅            | Код колоды Hearthstone (начинается с `AAE`) |

**Пример запроса:**

```bash
curl "https://your-server/public/render?deck=AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA" \
  --output deck.png
```

**Пример на Python:**

```python
import requests

deck_code = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
response = requests.get(
    "https://your-server/public/render",
    params={"deck": deck_code}
)
with open("deck.png", "wb") as f:
    f.write(response.content)
```

**Пример на JavaScript:**

```javascript
const deckCode = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";
const response = await fetch(`https://your-server/public/render?deck=${deckCode}`);
const blob = await response.blob();
const url = URL.createObjectURL(blob);
// url можно присвоить src у <img>
```

**Ответ:** PNG-изображение (`image/png`)

**Коды ошибок:**

| Код | Описание                              |
|-----|---------------------------------------|
| 200 | Успех — возвращает PNG                |
| 400 | Неверный или неподдерживаемый код колоды |

---

### 2. Получить метаданные колоды

Возвращает JSON с информацией о колоде: класс, формат, стоимость пыли, список карт.

```
GET /public/meta?deck=<код_колоды>
```

**Параметры:**

| Параметр | Тип    | Обязательный | Описание                                  |
|----------|--------|--------------|-------------------------------------------|
| `deck`   | string | ✅            | Код колоды Hearthstone (начинается с `AAE`) |

**Пример запроса:**

```bash
curl "https://your-server/public/meta?deck=AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
```

**Пример ответа:**

```json
{
  "deck_class": "Жрец",
  "deck_format": "Стандарт",
  "dust_cost": 11200,
  "card_count": 30,
  "cards": [
    { "dbf_id": 90749, "name": "E.T.C., Band Manager", "name_ru": "Э.Т.С., менеджер группы", "cost": 3, "count": 1, "rarity": "LEGENDARY" },
    ...
  ]
}
```

**Пример на Python:**

```python
import requests

deck_code = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
meta = requests.get(
    "https://your-server/public/meta",
    params={"deck": deck_code}
).json()

print(f"Класс: {meta['deck_class']}")
print(f"Формат: {meta['deck_format']}")
print(f"Стоимость пыли: {meta['dust_cost']}")
```

**Коды ошибок:**

| Код | Описание                              |
|-----|---------------------------------------|
| 200 | Успех — возвращает JSON               |
| 400 | Неверный или неподдерживаемый код колоды |

---

### 3. Список переводов архетипов

Возвращает полную таблицу переводов названий колод с английского на русский.

```
GET /public/archetypes
```

**Параметры:** нет

**Пример запроса:**

```bash
curl "https://your-server/public/archetypes"
```

**Пример ответа:**

```json
[
  { "eng": "Control Warrior", "rus": "Контроль Воин" },
  { "eng": "Miracle Rogue",   "rus": "Мираклл Разбойник" },
  { "eng": "Big Spell Mage",  "rus": "Большие заклинания Маг" },
  ...
]
```

**Пример на Python:**

```python
import requests

archetypes = requests.get("https://your-server/public/archetypes").json()
# Строим словарь eng -> rus
translation_map = {item["eng"]: item["rus"] for item in archetypes}

print(translation_map.get("Control Warrior", "Перевод не найден"))
# → "Контроль Воин"
```

**Пример на JavaScript:**

```javascript
const archetypes = await fetch("https://your-server/public/archetypes").then(r => r.json());
const map = Object.fromEntries(archetypes.map(a => [a.eng, a.rus]));
console.log(map["Control Warrior"]); // "Контроль Воин"
```

**Схема элемента:**

| Поле  | Тип    | Описание                    |
|-------|--------|-----------------------------|
| `eng` | string | Английское название архетипа |
| `rus` | string | Русское название архетипа    |

---

### 4. Перевести название колоды

Переводит одно название колоды с английского на русский. Если точного перевода нет — возвращает оригинал.

```
POST /public/archetypes/translate
Content-Type: application/json
```

**Тело запроса:**

```json
{ "name": "Control Warrior" }
```

| Поле   | Тип    | Обязательный | Описание                           |
|--------|--------|--------------|------------------------------------|
| `name` | string | ✅            | Название колоды для перевода       |

**Пример запроса:**

```bash
curl -X POST "https://your-server/public/archetypes/translate" \
  -H "Content-Type: application/json" \
  -d '{"name": "Control Warrior"}'
```

**Пример ответа (перевод найден):**

```json
{
  "original":   "Control Warrior",
  "translated": "Контроль Воин",
  "changed":    true
}
```

**Пример ответа (перевод не найден):**

```json
{
  "original":   "Some Unknown Deck",
  "translated": "Some Unknown Deck",
  "changed":    false
}
```

**Пример на Python:**

```python
import requests

response = requests.post(
    "https://your-server/public/archetypes/translate",
    json={"name": "Big Spell Mage"}
).json()

if response["changed"]:
    print(f"Переведено: {response['translated']}")
else:
    print("Перевод не найден, используем оригинал")
```

**Пример на JavaScript:**

```javascript
const result = await fetch("https://your-server/public/archetypes/translate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name: "Big Spell Mage" })
}).then(r => r.json());

console.log(result.translated); // "Большие заклинания Маг"
console.log(result.changed);    // true
```

**Схема ответа:**

| Поле         | Тип     | Описание                              |
|--------------|---------|---------------------------------------|
| `original`   | string  | Исходное название                     |
| `translated` | string  | Переведённое название (или оригинал)  |
| `changed`    | boolean | `true` если перевод был найден        |

---

## Установка и настройка

### Требования

- Python 3.10+
- Изображения карт Hearthstone (папка `cards/`, файлы вида `SW_001.png`)
- `cards.json` от [HearthstoneJSON](https://hearthstonejson.com/) (если не используется Blizzard API)

### 1. Клонирование репозитория

```bash
git clone https://github.com/Zulut30/deckview-telegram-bot.git
cd deckview-telegram-bot
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 3. Настройка окружения

Создайте файл `.env` (скопируйте из `.env.example`):

```bash
cp .env.example .env
```

Заполните переменные:

```env
# Обязательно
BOT_TOKEN=ваш_токен_бота      # Токен от @BotFather

# Пути к данным карт
IMAGES_PATH=cards             # Папка с PNG изображениями карт
JSON_PATH=cards.json          # База карт (HearthstoneJSON)
JSON_RU_PATH=cardsRU.json     # База с русскими названиями

# WordPress (для публикации на сайт)
WP_BASE_URL=https://your-site.com
WP_USER=wordpress_user
WP_APP_PASSWORD=app_password
WP_UPLOAD_ENABLED=1

# Telegram-канал для автопубликации (опционально)
CHANNEL_ID=@your_channel      # или числовой ID: -1001234567890
ADMIN_IDS=123456789           # Telegram ID администраторов (через запятую)

# API-ключ для приватных endpoints (опционально)
# Если не задан — приватные /render, /meta, /ingest тоже доступны без ключа
API_KEY=

# HSGuru парсер (автоматический постинг колод стримеров)
HSGURU_ENABLED=1
HSGURU_URL=https://www.hsguru.com/streamer-decks
HSGURU_INTERVAL_SECONDS=1800  # Интервал между публикациями (30 мин)
HSGURU_SEEN_PATH=cache/hsguru_seen.json

# Blizzard API (опционально, для актуальных данных карт)
BLIZZARD_ENABLED=1
BLIZZARD_CLIENT_ID=your_client_id
BLIZZARD_CLIENT_SECRET=your_client_secret
BLIZZARD_REGION=eu
BLIZZARD_LOCALE=en_US
BLIZZARD_LOCALE_RU=ru_RU
BLIZZARD_CACHE_DIR=cache/blizzard
BLIZZARD_CACHE_TTL_HOURS=24
```

---

## Запуск

### Запуск бота

```bash
python bot.py
```

### Запуск HTTP API (отдельно от бота)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

### Запуск с помощью systemd (production)

Создайте `/etc/systemd/system/deckview-bot.service`:

```ini
[Unit]
Description=Deckview Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/tg-manacost-bot
ExecStart=/home/ubuntu/tg-manacost-bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable deckview-bot
sudo systemctl start deckview-bot
```

### Обновление базы карт

```bash
python update_cards.py
```

---

## Структура проекта

```
tg-manacost-bot/
├── bot.py              # Основной файл Telegram-бота
├── api.py              # FastAPI HTTP API
├── config.py           # Загрузка конфигурации из .env
├── loader.py           # Загрузка и парсинг базы карт
├── generator.py        # Генерация изображений колод
├── database.py         # SQLite база данных (статистика, голоса)
├── hsguru_scraper.py   # Автопарсер колод с hsguru.com
├── wordpress.py        # Интеграция с WordPress REST API
├── blizzard_api.py     # Клиент Blizzard Hearthstone API
├── update_cards.py     # Скрипт обновления базы карт
├── Архетипы.csv        # Таблица переводов архетипов (EN → RU)
├── requirements.txt    # Зависимости Python
├── cards/              # PNG-изображения карт
├── cache/              # Кэш (изображения колод, API-ответы, seen)
└── templates/          # HTML-шаблоны (admin panel)
```

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветственное сообщение |
| `/help` | Справка по командам |
| `/image <название карты>` | Показать изображение карты |
| `/search_deck <название карты>` | Найти колоды с этой картой |
| `/wp <код колоды>` | Загрузить изображение в WordPress |

**Для администраторов:**

| Команда | Описание |
|---------|----------|
| `/admin` | Открыть панель управления |
| `/post` | Вручную опубликовать одну колоду с HSGuru |
| `/force_publish` | Принудительная публикация (и на сайт, и в канал) |

---

## Правила публикации колод

### На сайт WordPress
- **Интервал:** 1 колода каждые 30 минут
- **Минимум игр:** 20 (колоды с меньшим количеством пропускаются)
- **Дубликаты:** проверяются по трём критериям:
  1. Точное совпадение кода колоды
  2. Схожесть набора карт ≥ 90% (коэффициент Жаккара)
  3. Совпадение названия (кроме generic-имён: *Paladin*, *Mage*, *Warrior*, *Demon Hunter*, *Death Knight*, *Shaman* и т.д.)
- **Wild-фильтр:** не более одной Вольной колоды подряд

### В Telegram-канал
- **Интервал:** не чаще 1 раза в 2 часа
- Публикуется та же колода, что и на сайт (если прошло ≥ 2 часов)

---

## Лицензия

MIT
