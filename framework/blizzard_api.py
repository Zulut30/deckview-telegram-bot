import asyncio
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

OAUTH_URL = "https://us.battle.net/oauth/token"

# Синглтоны по паре (auth_token, locale) — один Session на процесс, без утечки FD.
_registry_lock = threading.Lock()
_registry: dict[tuple[str, str], "BlizzardAPI"] = {}


def get_blizzard_api(locale: str = "en_US") -> "BlizzardAPI":
    """Возвращает общий BlizzardAPI для данного locale (переиспользует Session и OAuth)."""
    from db.config import BATTLE_NET_TOKEN

    key = (BATTLE_NET_TOKEN or "", locale)
    with _registry_lock:
        api = _registry.get(key)
        if api is None:
            api = BlizzardAPI(BATTLE_NET_TOKEN, locale=locale)
            _registry[key] = api
        return api


class BlizzardAPI:
    def __init__(
        self,
        auth_token,
        locale="en_US",
        url="https://us.api.blizzard.com/hearthstone",
    ):
        if not auth_token or auth_token == "YOUR_TOKEN":
            raise ValueError(
                "BATTLE_NET_TOKEN не задан в .env. "
                "Получите client_id и client_secret на https://develop.battle.net/ "
                "и укажите Base64(client_id:client_secret)."
            )
        self.auth_token = auth_token
        self.locale = locale
        self.url = url

        self.session = requests.Session()
        self._token_lock = threading.Lock()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        """Закрыть HTTP-сессию (для тестов или явного завершения)."""
        self.session.close()

    def _fetch_token_unlocked(self) -> None:
        """POST OAuth; вызывать только под self._token_lock."""
        payload = "grant_type=client_credentials"
        headers = {
            "Authorization": f"Basic {self.auth_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with self.session.post(OAUTH_URL, headers=headers, data=payload, timeout=(3, 6)) as response:
            try:
                data = response.json()
            except ValueError as e:
                hint = ""
                if response.status_code == 401:
                    hint = " Неверный client_id или client_secret."
                elif not response.text or not response.text.strip():
                    hint = " Пустой ответ от сервера (сеть или таймаут?)."
                raise ValueError(
                    f"Blizzard OAuth вернул не JSON (код {response.status_code})."
                    f"{hint} Проверьте BATTLE_NET_TOKEN в .env — это Base64(client_id:client_secret)."
                ) from e
            if "access_token" not in data:
                msg = data.get("error_description", data.get("error", "Неверный BATTLE_NET_TOKEN"))
                raise ValueError(f"Blizzard OAuth: {msg}")
            self._access_token = data["access_token"]
            expires_in = int(data.get("expires_in") or 86400)
            # Запас до истечения, чтобы обновить токен заранее
            self._token_expires_at = time.monotonic() + max(120.0, float(expires_in) - 120.0)
            self.session.headers["Authorization"] = f"Bearer {self._access_token}"

    def _ensure_token(self) -> None:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return
        with self._token_lock:
            if self._access_token and time.monotonic() < self._token_expires_at:
                return
            self._fetch_token_unlocked()

    def _refresh_token_forced(self) -> None:
        with self._token_lock:
            self._access_token = None
            self._token_expires_at = 0.0
            self._fetch_token_unlocked()

    def convert_access_token(self):
        """Совместимость: однократно получить токен (тесты). Предпочтительнее get_blizzard_api()."""
        self._refresh_token_forced()
        return self._access_token

    async def get(self, *args, **kwargs):
        """Асинхронный GET с жёстким таймаутом и настраиваемым числом попыток.

        Важно: session.get() — синхронный вызов, поэтому запускается через asyncio.to_thread
        чтобы не блокировать event loop бота во время ожидания ответа Blizzard API.
        """
        kwargs.setdefault("timeout", (2, 4))
        attempt_limit = max(
            1,
            int(os.getenv("DECKVIEW_BLIZZARD_ATTEMPTS", "1")),
        )
        attempts = 0
        auth_refreshed = False
        while attempts < attempt_limit:
            try:

                def _do_get():
                    self._ensure_token()
                    return self.session.get(*args, **kwargs)

                response = await asyncio.to_thread(_do_get)
            except Exception as e:
                attempts += 1
                if attempts >= attempt_limit:
                    logger.warning("Blizzard API request failed after retries: %s", e)
                    return None
                await asyncio.sleep(0.25)
                continue

            if response.status_code == 401 and not auth_refreshed:
                response.close()
                await asyncio.to_thread(self._refresh_token_forced)
                auth_refreshed = True
                continue

            if response.ok:
                return response

            attempts += 1
            if attempts >= attempt_limit:
                return response
            await asyncio.sleep(0.25)
        return None

    async def get_from_code(self, deck_code):
        params = {
            "namespace": "static-us",
            "locale": self.locale,
            "code": deck_code,
        }
        response = await self.get(self.url + "/deck", params=params)
        if response is None:
            return {"error": "Нет ответа от API"}
        if not response.ok:
            try:
                err = response.json()
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("error") or err.get("statusMessage")
                    if msg is None:
                        msg = response.text or f"HTTP {response.status_code}"
                else:
                    msg = response.text or f"HTTP {response.status_code}"
            except Exception:
                msg = response.text or f"HTTP {response.status_code}"
            if isinstance(msg, dict):
                msg = msg.get("message", str(msg))
            logger.warning("Blizzard deck API error: %s", msg)
            return {"error": str(msg)}
        try:
            return response.json()
        except ValueError:
            logger.warning(
                "Blizzard deck API non-JSON response: %s",
                response.text[:200] if response.text else "empty",
            )
            return {"error": "Ответ API не в формате JSON (возможно, endpoint устарел или недоступен)."}

    async def get_card_from_id(self, card_id):
        params = {"namespace": "static-us", "locale": self.locale}
        response = await self.get(self.url + f"/cards/{card_id}", params=params)
        if response is None or not response.ok:
            return {"error": response.text if response else "Нет ответа"}
        try:
            return response.json()
        except ValueError:
            return {"error": "Неверный ответ API"}

    async def get_bgs_cards(self):
        """Fetch all Battlegrounds cards (heroes and minions)."""
        params = {
            "gameMode": "battlegrounds",
            "locale": self.locale,
            "pageSize": 500,
        }
        response = await self.get(self.url + "/cards", params=params)
        if response is None or not response.ok:
            return {"error": response.text if response else "Нет ответа"}
        try:
            return response.json()
        except ValueError:
            return {"error": "Неверный ответ API"}
