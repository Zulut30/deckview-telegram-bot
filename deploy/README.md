# Деплой на blizzcore.ru

## 0. Подключение домена (по шагам)

1. **DNS**
   У регистратора домена blizzcore.ru создайте **A-запись**: имя `@` (и при желании `www`) → **IP вашего сервера** (VPS, где крутится Deckview). Подождите 5–30 минут.

2. **Проверка, что веб-приложение запущено**
   На сервере:
   ```bash
   sudo systemctl status deckview-web
   ```
   Если сервиса нет — создайте его (раздел 3 ниже) и запустите. Должен слушать порт 5000.

3. **Nginx**
   Установите конфиг для blizzcore.ru (раздел 2 ниже), проверьте и перезагрузите nginx. После этого в браузере откройте:
   - `http://blizzcore.ru/` — главная (генератор колод),
   - `http://blizzcore.ru/dashboard` — дашборд бота.

4. **Если видите "Not Found"**
   - Убедитесь, что запрос идёт на тот же сервер, где настроен Nginx и запущен `deckview-web`.
   - Проверьте: `curl -I http://127.0.0.1:5000/` на сервере — должен быть ответ 200.
   - Откройте именно `/` или `/dashboard`, не другой путь.

5. **SSL (HTTPS)**
   Когда по `http://blizzcore.ru` открывается сайт: `sudo certbot --nginx -d blizzcore.ru -d www.blizzcore.ru`.

---

## 1. Запуск приложения (Gunicorn)

Из корня проекта:

```bash
cd /home/ubuntu/Deckview
./run_web.sh
```

Или с явным путём к venv:

```bash
.venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 --timeout 120 web_app:app
```

Рекомендуется запускать через systemd (см. ниже).

## 2. Подключение домена (Nginx)

1. Скопировать конфиг и включить сайт:
   ```bash
   sudo cp /home/ubuntu/Deckview/deploy/nginx-blizzcore.conf /etc/nginx/sites-available/blizzcore.ru
   sudo ln -sf /etc/nginx/sites-available/blizzcore.ru /etc/nginx/sites-enabled/
   ```
2. Проверить конфиг и перезагрузить Nginx:
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```
3. SSL (Let's Encrypt):
   ```bash
   sudo certbot --nginx -d blizzcore.ru -d www.blizzcore.ru
   ```
   После получения сертификата в конфиге раскомментировать блок `listen 443` и редирект с HTTP на HTTPS.

## 3. (Опционально) Systemd-сервис

Создать `/etc/systemd/system/deckview-web.service`:

```ini
[Unit]
Description=Deckview Web (Gunicorn)
After=network.target

[Service]
Type=notify
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/Deckview
EnvironmentFile=/home/ubuntu/Deckview/.env
ExecStart=/home/ubuntu/Deckview/.venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 --timeout 120 --access-logfile - --error-logfile - web_app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable deckview-web
sudo systemctl start deckview-web
```

В конфиге Nginx должен быть `proxy_pass http://127.0.0.1:5000`.
