<h1 align="center">🃏 Hearthstone Deck Visualizer</h1>

<p align="center">
  <b>Telegram-бот, HTTP API и автопубликатор колод Hearthstone.</b><br/>
  Определяет коды колод в сообщениях, рендерит красивые изображения и автоматически публикует колоды стримеров на сайт и в Telegram-канал.
</p>

<p align="center">
  <a href="https://hs-manacost.ru"><img alt="Website" src="https://img.shields.io/badge/Сайт-hs--manacost.ru-7B68EE?logo=wordpress&logoColor=white"></a>
  <a href="https://t.me/dcboom_hs"><img alt="Telegram channel" src="https://img.shields.io/badge/Канал-@dcboom__hs-2CA5E0?logo=telegram&logoColor=white"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white"></a>
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-yellow.svg">
  <img alt="Status" src="https://img.shields.io/badge/status-active-success">
</p>

---

## ✨ Возможности

- 🖼 **Генерация изображений колод** — высококачественный рендер по коду колоды Hearthstone
- 🤖 **Telegram-бот** — авто-распознавание кодов в чатах и личных сообщениях
- 🌐 **HTTP API** — публичные endpoints для рендера и метаданных (Swagger UI из коробки)
- 🔌 **WordPress-совместимость** — CORS, прямая публикация постов через REST API, готовые сниппеты для шорткодов
- 🌍 **Кросс-платформа** — готовые клиенты для Python, JavaScript/TypeScript, PHP, Go, Ruby, C#, Rust, cURL и shell
- 📰 **Автопостинг с HSGuru** — колоды стримеров публикуются на WordPress-сайт и в канал
- 🌍 **Перевод архетипов** — встроенная таблица EN → RU для названий колод
- 🛡 **Защита от дубликатов** — проверка по коду, схожести карт (Jaccard ≥ 90%) и названию
- ⚡ **Blizzard API + кэш** — актуальные данные карт с локальным кэшированием
- 🎛 **Админ-панель** — управление через веб-интерфейс и команды бота

---

## 📑 Содержание

- [Публичное API](#-публичное-api)
  - [1. Получить изображение колоды](#1-получить-изображение-колоды)
  - [2. Получить метаданные колоды](#2-получить-метаданные-колоды)
  - [3. Список переводов архетипов](#3-список-переводов-архетипов)
  - [4. Перевести название колоды](#4-перевести-название-колоды)
- [Установка и настройка](#установка-и-настройка)
- [Запуск](#запуск)
- [Структура проекта](#структура-проекта)
- [Команды бота](#команды-бота)
- [Правила публикации колод](#правила-публикации-колод)
- [Клиенты на разных языках](#-клиенты-на-разных-языках)
- [Интеграция с WordPress](#-интеграция-с-wordpress)
- [Диагностика](#-диагностика)
- [Лицензия](#лицензия)

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

- **Python 3.10–3.13** (протестировано на 3.13)
- Изображения карт Hearthstone (папка `cards/`, файлы вида `SW_001.png`)
- `cards.json` от [HearthstoneJSON](https://hearthstonejson.com/) — либо включённый Blizzard API
- Свободный порт `8000` (для HTTP API) и/или интернет (для Telegram-бота)

### 1. Клонирование репозитория

```bash
git clone https://github.com/Zulut30/deckview-telegram-bot.git
cd deckview-telegram-bot
```

### 2. Установка зависимостей

Рекомендуется использовать виртуальное окружение:

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

> 💡 На Python 3.13 убедитесь, что у вас установлен `setuptools` (`pip install setuptools`) — некоторые зависимости его требуют.

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

### Запуск Telegram-бота

```bash
python bot.py
```

### Запуск HTTP API (отдельно от бота)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

После запуска проверьте, что всё поднялось:

```bash
curl http://localhost:8000/public/archetypes | head -c 200
# должен прийти JSON-массив с парами {"eng": ..., "rus": ...}
```

Интерактивная документация Swagger UI будет доступна на `http://localhost:8000/docs`,
ReDoc — на `http://localhost:8000/redoc`.

### Быстрая проверка API (smoke test)

```bash
# 1. Документация открывается
curl -sf http://localhost:8000/docs > /dev/null && echo "✓ docs OK"

# 2. JSON-эндпоинт работает
curl -sf http://localhost:8000/public/archetypes > /dev/null && echo "✓ archetypes OK"

# 3. Невалидный код колоды → 400
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/public/meta?deck=bad"
# ожидаем: 400

# 4. Реальный код колоды → 200 (требуется загруженная база карт)
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://localhost:8000/public/render?deck=AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
# ожидаем: 200
```

### Публикация API наружу (reverse proxy)

Не публикуйте `uvicorn` напрямую в интернет. Поставьте перед ним nginx и terminate TLS:

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

### Запуск в продакшене через systemd

Создайте `/etc/systemd/system/deckview-bot.service` (Telegram-бот):

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

И `/etc/systemd/system/deckview-api.service` (HTTP API):

```ini
[Unit]
Description=Deckview HTTP API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/tg-manacost-bot
ExecStart=/home/ubuntu/tg-manacost-bot/venv/bin/uvicorn api:app \
          --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now deckview-bot deckview-api
sudo systemctl status deckview-bot deckview-api
```

### Обновление базы карт

```bash
python update_cards.py
```

---

## 🌍 Клиенты на разных языках

API — это обычный HTTP/JSON, поэтому работает из **любого современного языка и фреймворка**. Ниже — готовые сниппеты для самых популярных. Везде используется тот же тестовый код колоды; замените `https://api.example.com` на адрес вашего сервера.

<details>
<summary><b>🐍 Python (requests / httpx)</b></summary>

```python
import requests

DECK = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
BASE = "https://api.example.com"

# Получить метаданные
meta = requests.get(f"{BASE}/public/meta", params={"deck": DECK}, timeout=15).json()
print(meta["deck_class"], meta["dust_cost"])

# Сохранить PNG
img = requests.get(f"{BASE}/public/render", params={"deck": DECK}, timeout=30)
img.raise_for_status()
open("deck.png", "wb").write(img.content)
```

Асинхронно через `httpx`:

```python
import httpx, asyncio

async def fetch():
    async with httpx.AsyncClient(base_url="https://api.example.com", timeout=15) as c:
        r = await c.get("/public/meta", params={"deck": DECK})
        return r.json()

print(asyncio.run(fetch()))
```
</details>

<details>
<summary><b>🟨 JavaScript (браузер / Node.js fetch)</b></summary>

```javascript
const BASE = "https://api.example.com";
const DECK = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";

// Метаданные
const meta = await fetch(`${BASE}/public/meta?deck=${encodeURIComponent(DECK)}`).then(r => r.json());
console.log(meta.deck_class, meta.dust_cost);

// Изображение
const blob = await fetch(`${BASE}/public/render?deck=${encodeURIComponent(DECK)}`).then(r => r.blob());
document.querySelector("#deck").src = URL.createObjectURL(blob);
```

В Node.js 18+ `fetch` работает из коробки. Для CommonJS:

```javascript
const res = await fetch(`${BASE}/public/meta?deck=${encodeURIComponent(DECK)}`);
if (!res.ok) throw new Error(`HTTP ${res.status}`);
console.log(await res.json());
```
</details>

<details>
<summary><b>🟦 TypeScript (с типами)</b></summary>

```typescript
interface DeckCard {
  dbf_id: number;
  name: string;
  name_ru: string;
  cost: number;
  count: number;
  rarity: string;
}

interface DeckMeta {
  deck_class: string;
  deck_format: string;
  dust_cost: number;
  card_count: number;
  cards: DeckCard[];
}

async function getDeckMeta(deckCode: string): Promise<DeckMeta> {
  const res = await fetch(`https://api.example.com/public/meta?deck=${encodeURIComponent(deckCode)}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json() as Promise<DeckMeta>;
}
```
</details>

<details>
<summary><b>🐘 PHP (нативный + Guzzle + Laravel)</b></summary>

```php
<?php
$deck = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";
$base = "https://api.example.com";

// Метаданные через file_get_contents
$meta = json_decode(file_get_contents("$base/public/meta?deck=" . urlencode($deck)), true);
echo $meta['deck_class'], ' / Пыль: ', $meta['dust_cost'];

// PNG через cURL
$ch = curl_init("$base/public/render?deck=" . urlencode($deck));
curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => 1, CURLOPT_TIMEOUT => 30]);
file_put_contents("deck.png", curl_exec($ch));
curl_close($ch);
```

Через **Guzzle**:

```php
use GuzzleHttp\Client;

$client = new Client(['base_uri' => 'https://api.example.com', 'timeout' => 15]);
$meta   = json_decode($client->get('/public/meta', ['query' => ['deck' => $deck]])->getBody(), true);
```

Через **Laravel HTTP**:

```php
use Illuminate\Support\Facades\Http;

$meta = Http::timeout(15)->get('https://api.example.com/public/meta', ['deck' => $deck])->json();
```
</details>

<details>
<summary><b>🐹 Go</b></summary>

```go
package main

import (
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "net/url"
    "os"
)

type DeckMeta struct {
    DeckClass  string `json:"deck_class"`
    DeckFormat string `json:"deck_format"`
    DustCost   int    `json:"dust_cost"`
    CardCount  int    `json:"card_count"`
}

func main() {
    deck := "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
    base := "https://api.example.com"

    // Метаданные
    resp, err := http.Get(base + "/public/meta?deck=" + url.QueryEscape(deck))
    if err != nil { panic(err) }
    defer resp.Body.Close()

    var meta DeckMeta
    json.NewDecoder(resp.Body).Decode(&meta)
    fmt.Printf("%s / %d пыли\n", meta.DeckClass, meta.DustCost)

    // PNG
    img, _ := http.Get(base + "/public/render?deck=" + url.QueryEscape(deck))
    defer img.Body.Close()
    out, _ := os.Create("deck.png")
    io.Copy(out, img.Body)
    out.Close()
}
```
</details>

<details>
<summary><b>💎 Ruby</b></summary>

```ruby
require 'net/http'
require 'json'
require 'uri'

deck = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"

# Метаданные
uri  = URI("https://api.example.com/public/meta?deck=#{URI.encode_www_form_component(deck)}")
meta = JSON.parse(Net::HTTP.get(uri))
puts "#{meta['deck_class']} / #{meta['dust_cost']} пыли"

# PNG
img = Net::HTTP.get(URI("https://api.example.com/public/render?deck=#{URI.encode_www_form_component(deck)}"))
File.binwrite("deck.png", img)
```
</details>

<details>
<summary><b>🟣 C# / .NET</b></summary>

```csharp
using System.Net.Http;
using System.Text.Json;

var deck = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";
using var http = new HttpClient { BaseAddress = new Uri("https://api.example.com") };

// Метаданные
using var meta = await http.GetStreamAsync($"/public/meta?deck={Uri.EscapeDataString(deck)}");
using var doc  = await JsonDocument.ParseAsync(meta);
Console.WriteLine(doc.RootElement.GetProperty("deck_class").GetString());

// PNG
var png = await http.GetByteArrayAsync($"/public/render?deck={Uri.EscapeDataString(deck)}");
await File.WriteAllBytesAsync("deck.png", png);
```
</details>

<details>
<summary><b>🦀 Rust (reqwest)</b></summary>

```rust
use serde::Deserialize;

#[derive(Deserialize)]
struct DeckMeta {
    deck_class: String,
    dust_cost: u32,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let deck = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";
    let client = reqwest::Client::new();

    let meta: DeckMeta = client
        .get("https://api.example.com/public/meta")
        .query(&[("deck", deck)])
        .send().await?
        .json().await?;
    println!("{} / {} пыли", meta.deck_class, meta.dust_cost);

    let png = client
        .get("https://api.example.com/public/render")
        .query(&[("deck", deck)])
        .send().await?
        .bytes().await?;
    std::fs::write("deck.png", &png)?;
    Ok(())
}
```
</details>

<details>
<summary><b>☕ Java (HttpClient)</b></summary>

```java
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;

var deck = URLEncoder.encode("AAECAa0GBsubBOWwBI...", java.nio.charset.StandardCharsets.UTF_8);
var http = HttpClient.newHttpClient();

// Метаданные
var meta = http.send(
    HttpRequest.newBuilder(URI.create("https://api.example.com/public/meta?deck=" + deck)).build(),
    HttpResponse.BodyHandlers.ofString()
).body();
System.out.println(meta);

// PNG
http.send(
    HttpRequest.newBuilder(URI.create("https://api.example.com/public/render?deck=" + deck)).build(),
    HttpResponse.BodyHandlers.ofFile(Path.of("deck.png"))
);
```
</details>

<details>
<summary><b>🐚 cURL / Bash</b></summary>

```bash
DECK="AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA"
BASE="https://api.example.com"

# Метаданные → красивый JSON через jq
curl -sS "$BASE/public/meta?deck=$(printf %s "$DECK" | jq -sRr @uri)" | jq .

# Сохранить PNG
curl -sS "$BASE/public/render?deck=$(printf %s "$DECK" | jq -sRr @uri)" -o deck.png
```
</details>

<details>
<summary><b>⚡ Frameworks (React, Vue, Svelte, Next.js)</b></summary>

**React (hook):**
```jsx
import { useEffect, useState } from 'react';

export function useDeckMeta(deckCode) {
  const [meta, setMeta] = useState(null);
  useEffect(() => {
    if (!deckCode) return;
    fetch(`https://api.example.com/public/meta?deck=${encodeURIComponent(deckCode)}`)
      .then(r => r.json()).then(setMeta);
  }, [deckCode]);
  return meta;
}
```

**Vue 3 Composition API:**
```vue
<script setup>
import { ref, watchEffect } from 'vue';
const props = defineProps(['code']);
const meta = ref(null);
watchEffect(async () => {
  if (!props.code) return;
  meta.value = await fetch(`https://api.example.com/public/meta?deck=${encodeURIComponent(props.code)}`).then(r => r.json());
});
</script>
```

**Next.js Server Component:**
```jsx
export default async function DeckCard({ code }) {
  const meta = await fetch(
    `https://api.example.com/public/meta?deck=${encodeURIComponent(code)}`,
    { next: { revalidate: 3600 } }
  ).then(r => r.json());
  return <p>{meta.deck_class} — {meta.dust_cost} пыли</p>;
}
```
</details>

> 💡 **Совет:** для production-приложений всегда указывайте `timeout` и кэшируйте ответы. Эндпоинты `/public/meta` и `/public/render` дают одинаковый результат на одинаковый код колоды — отлично подходят для CDN-кэширования (Cache-Control: public, max-age=86400).

---

## 🔌 Интеграция с WordPress

API совместим с любыми WordPress-плагинами, дёргающими внешние REST-сервисы из браузера: **WPGetAPI**, **WP Webhooks**, **JetEngine REST Listing**, **Bricks Builder Query Loop**, **Elementor Dynamic Tags**, **Custom JS/HTML widgets** и т.д.

### CORS

В корне приложения настроен `CORSMiddleware`. Управляется через `.env`:

```env
# Разрешить всем (по умолчанию, удобно для публичного API)
CORS_ALLOW_ORIGINS=*

# Или ограничить конкретными доменами WP-сайта
CORS_ALLOW_ORIGINS=https://hs-manacost.ru,https://www.hs-manacost.ru
```

Разрешённые методы: `GET`, `POST`, `OPTIONS`. Заголовки `Content-Type`, `X-API-Key`, `Authorization` пропускаются. Preflight-кэш на 1 час.

### Вариант 1 — вставка изображения колоды через шорткод

Добавьте в `functions.php` темы:

```php
function manacost_deck_shortcode($atts) {
    $atts = shortcode_atts(['deck' => '', 'alt' => 'Hearthstone deck'], $atts);
    if (empty($atts['deck'])) return '';
    $url = 'https://api.example.com/public/render?deck=' . urlencode($atts['deck']);
    return sprintf(
        '<img src="%s" alt="%s" loading="lazy" style="max-width:100%%;height:auto" />',
        esc_url($url),
        esc_attr($atts['alt'])
    );
}
add_shortcode('hs_deck', 'manacost_deck_shortcode');
```

Использование в любом редакторе:

```
[hs_deck deck="AAECAa0GBsubBOWwBI..." alt="Контроль Воин"]
```

### Вариант 2 — REST-прокси на стороне WordPress

Чтобы клиент стучался в свой же домен (никаких CORS-проблем даже у самых старых браузеров):

```php
add_action('rest_api_init', function () {
    register_rest_route('manacost/v1', '/deck/(?P<code>[A-Za-z0-9+/=]+)', [
        'methods'  => 'GET',
        'callback' => function ($req) {
            $code = $req['code'];
            $resp = wp_remote_get("https://api.example.com/public/meta?deck=" . rawurlencode($code), ['timeout' => 15]);
            if (is_wp_error($resp)) return new WP_Error('upstream', $resp->get_error_message(), ['status' => 502]);
            return new WP_REST_Response(json_decode(wp_remote_retrieve_body($resp), true), wp_remote_retrieve_response_code($resp));
        },
        'permission_callback' => '__return_true',
    ]);
});
```

После активации: `GET /wp-json/manacost/v1/deck/AAECAa0G...`

### Вариант 3 — клиентский JS (Elementor / Bricks / любой HTML-блок)

```html
<div id="deck-meta"></div>
<script>
(async () => {
  const code = "AAECAa0GBsubBOWwBIWfBYGhBaChBbyhBQyY6wOtigSJowSktgShtgSHtwTbuQT++QT9+wSUoQX9ogW8owUA";
  const res  = await fetch(`https://api.example.com/public/meta?deck=${encodeURIComponent(code)}`);
  if (!res.ok) return;
  const meta = await res.json();
  document.getElementById('deck-meta').innerHTML = `
    <p>Класс: <b>${meta.deck_class}</b></p>
    <p>Формат: <b>${meta.deck_format}</b></p>
    <p>Пыль: <b>${meta.dust_cost}</b></p>`;
})();
</script>
```

### Вариант 4 — конфигурация WPGetAPI

| Поле | Значение |
|------|----------|
| API URL | `https://api.example.com` |
| Endpoint | `/public/meta` |
| Method | `GET` |
| Query parameter | `deck` = `{{shortcode_arg:deck}}` |
| Headers | (не нужны) |
| Output | `[wpgetapi_endpoint api_id="manacost" endpoint_id="meta" debug="0"]` |

> ⚠️ Если API закрыт API-ключом — добавьте header `X-API-Key: <ваш ключ>` в настройках плагина. CORS уже разрешает этот заголовок.

### Автопубликация постов в WordPress

Бот сам умеет создавать посты с колодами через WP REST API. Включается переменными `WP_BASE_URL`, `WP_USER`, `WP_APP_PASSWORD` в `.env` — см. [Установка и настройка](#установка-и-настройка). Application Password создаётся в админке WP: *Профиль → Application Passwords*.

---

## 🩺 Диагностика

| Симптом | Что проверить |
|---------|---------------|
| `ModuleNotFoundError: pkg_resources` | Обновите репозиторий — заменено на `importlib.metadata`. Если код старый: `pip install "setuptools<81"` |
| `BOT_TOKEN не установлен` при запуске API | Файл `.env` существует в рабочей директории и содержит `BOT_TOKEN=...` (значение может быть любым, если бот не используется) |
| `/public/render` возвращает 400 на валидный код | Не загружена база карт. Запустите `python update_cards.py` или включите Blizzard API |
| Логи замусорены `binascii.Error` traceback'ами | Ожидаемо при невалидных кодах от пользователей — API всё равно отдаёт корректный 400 |
| Порт 8000 занят | `uvicorn api:app --port 8001` или `lsof -i:8000` чтобы найти процесс |
| WP-плагин получает `CORS error` в консоли | Проверьте `CORS_ALLOW_ORIGINS` в `.env` — домен сайта должен быть в списке (или `*`). Не забудьте перезапустить `uvicorn` после правки `.env` |
| Preflight (OPTIONS) возвращает 405 | Старая версия `api.py` без `CORSMiddleware` — обновите репозиторий до последнего коммита |

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

Проект распространяется под лицензией **MIT** — свободно используйте, изменяйте и распространяйте с указанием авторства.

---

<p align="center">
  Сделано с ❤️ для сообщества Hearthstone<br/>
  <sub>Если проект полезен — поставьте ⭐ на GitHub</sub>
</p>
