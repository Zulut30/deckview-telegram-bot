"""
Единый requests.Session для исходящих HTTP-запросов (кроме Blizzard OAuth/API и cloudscraper).

Переиспользование пула соединений urllib3 снижает риск исчерпания FD при сериях
запросов (скачивание артов колоды, HSJSON, wiki fallback и т.д.).
"""
from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter

_lock = threading.Lock()
_session: requests.Session | None = None

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 DeckviewBot/1.0"
)


def get_http_session() -> requests.Session:
    """Потокобезопасный синглтон Session (один на процесс)."""
    global _session
    with _lock:
        if _session is None:
            s = requests.Session()
            s.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
            adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session
