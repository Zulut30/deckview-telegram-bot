"""
Минимальный парсер HSGuru для команды /publish: взять одну новую колоду и вернуть payload.
Дубликаты только по коду колоды (без hearthstone.deckstrings).
Перевод названий колод загружается из опубликованной Google Таблицы (архетипы).
"""
import csv
import io
import asyncio
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    ARCHETYPES_SHEET_URL,
    HSGURU_BROWSER_FALLBACK,
    HSGURU_BROWSER_PATH,
    HSGURU_BROWSER_TIMEOUT,
    HSGURU_CF_CLEARANCE,
    HSGURU_COOKIES,
    HSGURU_FETCH_BACKOFF_SECONDS,
    HSGURU_FETCH_RETRIES,
    HSGURU_FALLBACK_URLS,
    HSGURU_FETCH_TIMEOUT,
    HSGURU_LOCK_PATH,
    HSGURU_MIN_GAMES,
    HSGURU_MIN_PARSED_DECKS,
    HSGURU_PUBLISH_BATCH_LIMIT,
    HSGURU_PROXY_URLS,
    HSGURU_SEEN_PATH,
    HSGURU_STATUS_PATH,
    HSGURU_STREAMER_OFFSETS,
    HSGURU_STREAMER_PAGE_LIMIT,
    HSGURU_USER_AGENT,
    HSGURU_URL,
    HS_DATA_API_ENABLED,
)

try:
    import cloudscraper
    _has_cloudscraper = True
except ImportError:
    _has_cloudscraper = False

try:
    from crawlee.http_clients import ImpitHttpClient
    _has_crawlee = True
except ImportError:
    ImpitHttpClient = None
    _has_crawlee = False

_cloudscraper_instance = None


def _get_cloudscraper():
    global _cloudscraper_instance
    if _cloudscraper_instance is None and _has_cloudscraper:
        _cloudscraper_instance = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            }
        )
    return _cloudscraper_instance

from bs4 import BeautifulSoup

from framework.http_session import get_http_session

try:
    from hs_data_api import HSDataAPIError, get_streamer_decks as get_data_api_streamer_decks
except Exception:
    HSDataAPIError = RuntimeError
    get_data_api_streamer_decks = None

FORMAT_MAP = {
    "Standard": "Стандарт",
    "Wild": "Вольный",
    "Classic": "Классический",
    "Twist": "Потасовка",
}

MIN_GAMES = HSGURU_MIN_GAMES

HSGURU_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Priority": "u=0, i",
    "Sec-CH-UA": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "Sec-CH-UA-Arch": '"x86"',
    "Sec-CH-UA-Bitness": '"64"',
    "Sec-CH-UA-Full-Version": '"148.0.7778.97"',
    "Sec-CH-UA-Full-Version-List": '"Chromium";v="148.0.7778.97", "Google Chrome";v="148.0.7778.97", "Not/A)Brand";v="99.0.0.0"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Model": '""',
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-CH-UA-Platform-Version": '"19.0.0"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": HSGURU_USER_AGENT,
}


class HSGuruFetchError(RuntimeError):
    pass


def _seen_path() -> Path:
    p = Path(HSGURU_SEEN_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _status_path() -> Path:
    p = Path(HSGURU_STATUS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _lock_path() -> Path:
    p = Path(HSGURU_LOCK_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp_path, 0o664)
    except OSError:
        pass
    os.replace(tmp_path, path)


def _write_status(payload: Dict[str, Any]) -> None:
    try:
        payload.setdefault("created_at", _now_iso())
        _atomic_write_json(_status_path(), payload)
    except Exception as e:
        print(f"[HSGuru] status write failed: {e}")


def load_seen() -> Dict[str, Any]:
    path = _seen_path()
    if not path.exists():
        return {"codes": set(), "decks": {}, "last_published_format": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"codes": set(data), "decks": {}, "last_published_format": ""}
        return {
            "codes": set(data.get("codes", [])),
            "decks": data.get("decks", {}),
            "last_published_format": data.get("last_published_format", ""),
        }
    except Exception as e:
        import traceback, shutil, datetime
        print(f"[HSGuru] ❌ ОШИБКА: не удалось прочитать {path}: {e}")
        traceback.print_exc()
        try:
            bak = str(path) + f".corrupted.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(str(path), bak)
            print(f"[HSGuru] Резервная копия повреждённого файла: {bak}")
        except Exception:
            pass
        return {"codes": set(), "decks": {}, "last_published_format": ""}


def save_seen(seen_data: Dict[str, Any]) -> None:
    path = _seen_path()
    data = {
        "codes": list(seen_data["codes"]),
        "decks": {
            k: {
                "published_at": v.get("published_at", ""),
                "format": v.get("format", ""),
            }
            for k, v in seen_data.get("decks", {}).items()
        },
        "last_published_format": seen_data.get("last_published_format", ""),
    }
    _atomic_write_json(path, data)


def _response_text_or_error(response: Any, source: str) -> str:
    status = int(getattr(response, "status_code", 0) or 0)
    text = getattr(response, "text", "") or ""
    if status >= 400:
        snippet = text[:220].replace("\n", " ").strip()
        raise HSGuruFetchError(f"{source} HTTP {status}: {snippet}")
    if not text.strip():
        raise HSGuruFetchError(f"{source} returned empty HTML")
    return text


def _deck_code_pattern() -> re.Pattern[str]:
    return re.compile(r"\bAAE[A-Za-z0-9+/]{40,}={0,3}")


def _looks_like_streamer_decks_html(html: str) -> bool:
    value = html or ""
    if len(value) < 1000:
        return False
    lowered = value.lower()
    cloudflare_markers = (
        "cf-browser-verification",
        "checking if the site connection is secure",
        "just a moment",
        "cloudflare ray id",
        "error 1020",
        "access denied",
    )
    if any(marker in lowered for marker in cloudflare_markers):
        return False
    return (
        "data-clipboard-text" in value
        or ("/deck/" in value and ("<table" in lowered or "streamer" in lowered))
        or ("peaked by:" in lowered and _deck_code_pattern().search(value) is not None)
    )


def _headers() -> Dict[str, str]:
    headers = dict(HSGURU_HEADERS)
    cookie_header = _cookie_header()
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _cookie_header() -> str:
    cookie_values = []
    if HSGURU_COOKIES:
        cookie_values.append(HSGURU_COOKIES)
    if HSGURU_CF_CLEARANCE and "cf_clearance=" not in HSGURU_CF_CLEARANCE:
        cookie_values.append(f"cf_clearance={HSGURU_CF_CLEARANCE}")
    elif HSGURU_CF_CLEARANCE:
        cookie_values.append(HSGURU_CF_CLEARANCE)

    cookies: List[str] = []
    for cookie_header in cookie_values:
        for part in cookie_header.split(";"):
            part = part.strip().strip(";")
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name in {"cf_clearance", "__cf_bm"} and _looks_like_expired_cf_cookie(value):
                continue
            cookies.append(f"{name}={value.strip()}")
    return "; ".join(cookies)


def _looks_like_expired_cf_cookie(value: str) -> bool:
    """Best-effort guard against stale Cloudflare cookies saved in .env."""
    match = re.search(r"-(\d{10})[.-]", value or "")
    if not match:
        return False
    try:
        issued_at = int(match.group(1))
    except ValueError:
        return False
    return issued_at < int(time.time()) - 20 * 60 * 60


def _cookie_pairs() -> Dict[str, str]:
    cookie_header = _cookie_header()
    if not cookie_header:
        return {}
    parsed = SimpleCookie()
    try:
        parsed.load(cookie_header)
    except Exception:
        pairs: Dict[str, str] = {}
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name:
                pairs[name] = value.strip()
        return pairs
    return {key: morsel.value for key, morsel in parsed.items()}


def _apply_browser_cookies(page: Any, url: str) -> None:
    cookie_pairs = _cookie_pairs()
    if not cookie_pairs:
        return
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or "www.hsguru.com"
    cookie_url = f"{scheme}://{host}/"
    page.run_cdp("Network.enable")
    for name, value in cookie_pairs.items():
        page.run_cdp(
            "Network.setCookie",
            name=name,
            value=value,
            url=cookie_url,
            domain=host,
            path="/",
            secure=(scheme == "https"),
        )


def _proxy_attempts() -> List[tuple[str, Optional[Dict[str, str]]]]:
    attempts: List[tuple[str, Optional[Dict[str, str]]]] = [("direct", None)]
    for proxy in HSGURU_PROXY_URLS:
        attempts.append((proxy, {"http": proxy, "https": proxy}))
    return attempts


def _browser_proxy_values() -> List[Optional[str]]:
    values: List[Optional[str]] = [None]
    for proxy in HSGURU_PROXY_URLS:
        browser_proxy = proxy.replace("socks5h://", "socks5://")
        if browser_proxy not in values:
            values.append(browser_proxy)
    return values


def _candidate_urls() -> List[str]:
    urls = [HSGURU_URL]
    for url in HSGURU_FALLBACK_URLS:
        if url not in urls:
            urls.append(url)
    return urls


def _with_query_params(url: str, params: Dict[str, Any]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None or value == "":
            query.pop(key, None)
        else:
            query[key] = str(value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _streamer_page_urls(url: str) -> List[str]:
    if "streamer-decks" not in url:
        return [url]

    offsets = HSGURU_STREAMER_OFFSETS or (0,)
    urls: List[str] = []
    for offset in offsets:
        params: Dict[str, Any] = {"limit": HSGURU_STREAMER_PAGE_LIMIT}
        if int(offset) > 0:
            params["offset"] = int(offset)
        candidate = _with_query_params(url, params)
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _fetch_with_cloudscraper(url: str) -> str:
    if not _has_cloudscraper:
        raise HSGuruFetchError("cloudscraper is not installed")
    scraper = _get_cloudscraper()
    errors = []
    for proxy_label, proxies in _proxy_attempts():
        try:
            response = scraper.get(url, headers=_headers(), proxies=proxies, timeout=HSGURU_FETCH_TIMEOUT)
            return _response_text_or_error(response, f"cloudscraper/{proxy_label}")
        except Exception as e:
            errors.append(str(e))
    raise HSGuruFetchError("; ".join(errors))


def _fetch_with_crawlee(url: str) -> str:
    if not _has_crawlee or ImpitHttpClient is None:
        raise HSGuruFetchError("crawlee is not installed")

    async def _run() -> str:
        client = ImpitHttpClient(browser="chrome")
        try:
            response = await client.send_request(
                url,
                headers=_headers(),
                timeout=timedelta(seconds=HSGURU_FETCH_TIMEOUT),
            )
            raw = await response.read()
            text = raw.decode("utf-8", "replace")
            if int(response.status_code) >= 400:
                snippet = text[:220].replace("\n", " ").strip()
                raise HSGuruFetchError(f"crawlee/impit HTTP {response.status_code}: {snippet}")
            if not text.strip():
                raise HSGuruFetchError("crawlee/impit returned empty HTML")
            return text
        finally:
            await client.cleanup()

    return asyncio.run(_run())


def _fetch_with_requests(url: str) -> str:
    errors = []
    for proxy_label, proxies in _proxy_attempts():
        try:
            response = get_http_session().get(url, headers=_headers(), proxies=proxies, timeout=HSGURU_FETCH_TIMEOUT)
            return _response_text_or_error(response, f"requests/{proxy_label}")
        except Exception as e:
            errors.append(str(e))
    raise HSGuruFetchError("; ".join(errors))


def _fetch_with_drissionpage(url: str) -> str:
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError as e:
        raise HSGuruFetchError(f"DrissionPage is not installed: {e}") from e

    errors = []
    for proxy in _browser_proxy_values():
        options = ChromiumOptions()
        if HSGURU_BROWSER_PATH and os.path.exists(HSGURU_BROWSER_PATH):
            options.set_browser_path(HSGURU_BROWSER_PATH)
        try:
            options.auto_port()
        except Exception:
            pass
        options.headless(True)
        options.no_imgs()
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--window-size=1365,900",
        ):
            options.set_argument(arg)

        if HSGURU_USER_AGENT:
            options.set_user_agent(HSGURU_USER_AGENT)
        options.set_argument("--disable-blink-features=AutomationControlled")
        if proxy:
            options.set_argument(f"--proxy-server={proxy}")

        page = ChromiumPage(addr_or_opts=options)
        try:
            _apply_browser_cookies(page, url)
            page.get(url, timeout=HSGURU_BROWSER_TIMEOUT)
            deadline = time.time() + HSGURU_BROWSER_TIMEOUT
            last_html = ""
            while time.time() < deadline:
                last_html = page.html or ""
                if _looks_like_streamer_decks_html(last_html):
                    return last_html
                time.sleep(1)
            raise HSGuruFetchError(f"DrissionPage returned HTML without streamer deck markers via {proxy or 'direct'}")
        except Exception as e:
            errors.append(str(e))
        finally:
            page.quit()
    raise HSGuruFetchError("; ".join(errors))


def _fetch_single_html(url: str) -> str:
    errors = []
    methods = [
        ("crawlee", _fetch_with_crawlee),
        ("cloudscraper", _fetch_with_cloudscraper),
        ("requests", _fetch_with_requests),
    ]
    if HSGURU_BROWSER_FALLBACK:
        methods.append(("drissionpage", _fetch_with_drissionpage))

    for attempt in range(1, HSGURU_FETCH_RETRIES + 1):
        for name, fetcher in methods:
            try:
                html = fetcher(url)
                if not _looks_like_streamer_decks_html(html):
                    raise HSGuruFetchError(f"{name} returned HTML without streamer deck markers")
                print(
                    f"[HSGuru] fetch ok via {name}, attempt={attempt}, "
                    f"url={url}, html_len={len(html)}"
                )
                return html
            except Exception as e:
                message = f"{url} {name} attempt={attempt}: {e}"
                errors.append(message)
                print(f"[HSGuru] fetch failed via {name}, attempt={attempt}, url={url}: {e}")
        if attempt < HSGURU_FETCH_RETRIES and HSGURU_FETCH_BACKOFF_SECONDS > 0:
            time.sleep(HSGURU_FETCH_BACKOFF_SECONDS * attempt)

    raise HSGuruFetchError("; ".join(errors) or "all HSGuru fetchers failed")


def fetch_html() -> str:
    errors = []
    for url in _candidate_urls():
        try:
            return _fetch_single_html(url)
        except Exception as e:
            errors.append(str(e))
    raise HSGuruFetchError("; ".join(errors) or "all HSGuru fetchers failed")


def fetch_streamer_pages() -> List[str]:
    html_pages: List[str] = []
    errors: List[str] = []

    for base_url in _candidate_urls():
        for url in _streamer_page_urls(base_url):
            try:
                html_pages.append(_fetch_single_html(url))
            except Exception as e:
                errors.append(f"{url}: {e}")
                print(f"[HSGuru] page fetch failed, url={url}: {e}")

    if not html_pages:
        raise HSGuruFetchError("; ".join(errors) or "all HSGuru page fetchers failed")

    return html_pages


def _extract_legend_rank(peak_value: str) -> str:
    if not peak_value:
        return ""
    m = re.search(r"\d+", peak_value.replace(",", ""))
    return m.group(0) if m else ""


def _nearby_container(node: Any) -> Any:
    row = node.find_parent("tr")
    if row is not None:
        return row
    current = node
    best = node
    for _ in range(8):
        parent = current.find_parent()
        if parent is None:
            break
        best = parent
        try:
            if parent.select_one('a[href*="/deck/"]'):
                return parent
        except Exception:
            pass
        current = parent
    return best


def _first_deck_link_text(container: Any) -> str:
    link = container.select_one('a[href^="/deck/"], a[href*="/deck/"]')
    if link is not None:
        return link.get_text(" ", strip=True)
    for link in container.find_all("a"):
        text = link.get_text(" ", strip=True)
        if text and len(text) <= 80:
            return text
    return ""


def _parse_decks_from_clipboards(soup: BeautifulSoup, archetypes: Dict[str, str]) -> List[Dict[str, Any]]:
    decks: List[Dict[str, Any]] = []
    seen_codes = set()
    for clip in soup.select("[data-clipboard-text]"):
        deck_code = (clip.get("data-clipboard-text") or "").strip()
        if not deck_code or len(deck_code) < 20 or deck_code in seen_codes:
            continue
        seen_codes.add(deck_code)
        container = _nearby_container(clip)
        text = container.get_text(" ", strip=True)
        deck_name_en = _first_deck_link_text(container)
        deck_name = translate_deck_name(deck_name_en, archetypes) if deck_name_en else "Deck"
        format_match = re.search(r"\b(Standard|Wild|Classic|Twist)\b", text, re.IGNORECASE)
        format_cell = format_match.group(1).title() if format_match else ""
        wl_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", text)
        wins = int(wl_match.group(1)) if wl_match else 0
        losses = int(wl_match.group(2)) if wl_match else 0
        peak_match = re.search(r"\bPeak\b\s*#?\s*([0-9,]+)", text, re.IGNORECASE)
        peak = peak_match.group(1) if peak_match else ""
        decks.append({
            "deck_code": deck_code,
            "deck_name": deck_name,
            "streamer": "",
            "format": format_cell,
            "wins": wins,
            "losses": losses,
            "total_games": wins + losses,
            "peak": peak,
            "latest": "",
            "worst": "",
            "legend_rank": _extract_legend_rank(peak),
        })
    return decks


def _parse_decks_from_text(soup: BeautifulSoup, archetypes: Dict[str, str]) -> List[Dict[str, Any]]:
    text = soup.get_text("\n", strip=True)
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    decks: List[Dict[str, Any]] = []
    seen_codes = set()
    code_re = _deck_code_pattern()
    for index, line in enumerate(lines):
        code_match = code_re.search(line)
        if not code_match:
            continue
        deck_code = code_match.group(0)
        if deck_code in seen_codes:
            continue
        seen_codes.add(deck_code)

        window_start = max(0, index - 12)
        window_end = min(len(lines), index + 8)
        window = lines[window_start:window_end]
        window_text = "\n".join(window)

        name = ""
        same_line_name = re.search(r"([A-Za-z][A-Za-z0-9 ':/().-]{2,80})\s+" + re.escape(deck_code), line)
        if same_line_name:
            name = same_line_name.group(1).strip(" #")
        if not name:
            for near in lines[index + 1:window_end]:
                if deck_code in near:
                    before_code = near.split(deck_code, 1)[0].strip(" #")
                    before_code = re.sub(r"^\W+", "", before_code).strip()
                    if 2 < len(before_code) <= 80:
                        name = before_code
                        break
        if not name:
            name = "Deck"
        deck_name = translate_deck_name(name, archetypes) if name else "Deck"

        streamer = ""
        for marker in ("First Streamed:", "Peaked By:"):
            try:
                pos = window.index(marker)
                if pos + 1 < len(window):
                    streamer = window[pos + 1]
                    break
            except ValueError:
                continue

        peak = ""
        peak_match = re.search(r"Peaked By:\s*\n[^\n]+\n([0-9,]+)", window_text, re.IGNORECASE)
        if peak_match:
            peak = peak_match.group(1)

        streamed = 0
        streamed_match = re.search(r"#\s*Streamed:\s*([0-9,]+)", window_text, re.IGNORECASE)
        if streamed_match:
            try:
                streamed = int(streamed_match.group(1).replace(",", ""))
            except ValueError:
                streamed = 0

        decks.append({
            "deck_code": deck_code,
            "deck_name": deck_name,
            "streamer": streamer,
            "format": "",
            "wins": 0,
            "losses": 0,
            "total_games": 0,
            "streamed_count": streamed,
            "stats_unavailable": True,
            "peak": peak,
            "latest": "",
            "worst": "",
            "legend_rank": _extract_legend_rank(peak),
        })
    return decks


# ---------------------------------------------------------------------------
# Архетипы: загрузка из Google Таблицы и перевод названий колод
# ---------------------------------------------------------------------------

def load_archetypes() -> Dict[str, str]:
    """
    Загружает таблицу перевода архетипов (English -> Russian).
    Сначала пробует локальную БД, при пустой таблице — из Google Таблицы.
    """
    try:
        from web_db import get_all_archetypes as _db_archetypes
        rows = _db_archetypes()
        if rows:
            result = {r["name_en"].lower(): r["name_ru"] for r in rows}
            if result:
                print(f"[HSGuru] Загружено {len(result)} переводов архетипов из БД")
                return result
    except Exception:
        pass

    translations: Dict[str, str] = {}
    if not ARCHETYPES_SHEET_URL:
        return translations

    def _parse_csv(text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 3:
                eng = row[1].strip().strip('"')
                rus = row[2].strip().strip('"')
                if eng and rus and "англ" not in eng.lower() and "названия" not in eng.lower():
                    out[eng.lower()] = rus
        return out

    def _parse_html(html: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) >= 3:
                    eng = cells[1].get_text(strip=True).strip('"')
                    rus = cells[2].get_text(strip=True).strip('"')
                    if eng and rus and "англ" not in eng.lower() and "названия" not in eng.lower():
                        out[eng.lower()] = rus
        return out

    try:
        if "pubhtml" in ARCHETYPES_SHEET_URL:
            csv_url = ARCHETYPES_SHEET_URL.replace("/pubhtml", "/pub?output=csv")
            resp = get_http_session().get(
                csv_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"},
                timeout=15,
            )
            if resp.status_code == 200:
                resp.encoding = 'utf-8' # Явно задаем кодировку для Google Таблиц
                if resp.text.strip():
                    translations = _parse_csv(resp.text)

        if not translations:
            resp = get_http_session().get(
                ARCHETYPES_SHEET_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            resp.encoding = 'utf-8' # На случай, если HTML тоже придет без чарсета
            translations = _parse_html(resp.text)
        if translations:
            print(f"[HSGuru] Загружено {len(translations)} переводов архетипов из таблицы")
    except Exception as e:
        print(f"[HSGuru] Ошибка загрузки архетипов из таблицы: {e}")
    return translations


def translate_deck_name(name: str, archetypes: Dict[str, str]) -> str:
    """Переводит название колоды с английского на русский.

    Приоритеты:
    1. Точное совпадение (без учёта регистра).
    2. Самое длинное частичное совпадение — ключи проверяются от длинных к коротким,
       чтобы «Tempo Rogue» нашёлся раньше, чем просто «Rogue».
    """
    if not name or not archetypes:
        return name

    name_lower = name.lower().strip()

    # 1. Точное совпадение
    if name_lower in archetypes:
        return archetypes[name_lower]

    # 2. Самое длинное частичное совпадение (длинные ключи проверяются первыми)
    for eng in sorted(archetypes.keys(), key=len, reverse=True):
        if eng in name_lower:
            return archetypes[eng]

    return name


def parse_decks(html: str, archetypes: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    archetypes = archetypes or {}
    soup = BeautifulSoup(html, "html.parser")
    decks = []
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        try:
            deck_link = row.select_one('a[href^="/deck/"]')
            deck_name_en = deck_link.get_text(strip=True) if deck_link else ""
            deck_name = translate_deck_name(deck_name_en, archetypes)
            streamer = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            format_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            peak = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            latest = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            worst = cells[5].get_text(strip=True) if len(cells) > 5 else ""
            legend_rank = _extract_legend_rank(peak)
            wins, losses = 0, 0
            if len(cells) > 6:
                wl = cells[6].get_text(strip=True)
                match = re.match(r"(\d+)\s*-\s*(\d+)", wl)
                if match:
                    wins, losses = int(match.group(1)), int(match.group(2))
            clip = row.select_one("[data-clipboard-text]")
            deck_code = clip.get("data-clipboard-text", "") if clip else ""
            if not deck_code or not deck_name:
                continue
            decks.append({
                "deck_code": deck_code,
                "deck_name": deck_name,
                "streamer": streamer,
                "format": format_cell,
                "wins": wins,
                "losses": losses,
                "total_games": wins + losses,
                "peak": peak,
                "latest": latest,
                "worst": worst,
                "legend_rank": legend_rank,
            })
        except Exception:
            continue
    if not decks:
        decks = _parse_decks_from_clipboards(soup, archetypes)
    if not decks:
        decks = _parse_decks_from_text(soup, archetypes)
    return decks


def _deck_quality_score(deck: Dict[str, Any]) -> tuple[int, int, int, int]:
    wins = int(deck.get("wins") or 0)
    losses = int(deck.get("losses") or 0)
    total = int(deck.get("total_games") or wins + losses)
    filled_stats = 1 if wins or losses else 0
    filled_streamer = 1 if (deck.get("streamer") or "").strip() else 0
    filled_format = 1 if (deck.get("format") or "").strip() else 0
    return (total, filled_stats, filled_streamer, filled_format)


def dedupe_decks(decks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by deck code and keep the row with the richest stats."""
    ordered_codes: List[str] = []
    by_code: Dict[str, Dict[str, Any]] = {}
    for deck in decks:
        code = (deck.get("deck_code") or "").strip()
        if not code:
            continue
        deck["deck_code"] = code
        if code not in by_code:
            ordered_codes.append(code)
            by_code[code] = deck
            continue
        if _deck_quality_score(deck) > _deck_quality_score(by_code[code]):
            by_code[code] = deck
    return [by_code[code] for code in ordered_codes]


def is_publishable_deck(deck: Dict[str, Any]) -> bool:
    """Строгий фильтр ленты: больше 20 игр и положительный винрейт."""
    wins = int(deck.get("wins") or 0)
    losses = int(deck.get("losses") or 0)
    total = int(deck.get("total_games") or wins + losses)
    return total > MIN_GAMES and wins > losses


def _deck_to_payload(deck: Dict[str, Any]) -> Dict[str, Any]:
    mode = FORMAT_MAP.get(deck.get("format", ""), deck.get("format", ""))
    payload = {
        "deck_code": deck["deck_code"],
        "deck_name": deck["deck_name"],
        "streamer": deck.get("streamer", ""),
        "player": deck.get("streamer", ""),
        "format": deck.get("format", ""),
        "wins": int(deck.get("wins") or 0),
        "losses": int(deck.get("losses") or 0),
        "peak": deck.get("peak", ""),
        "latest": deck.get("latest", ""),
        "worst": deck.get("worst", ""),
        "legend_rank": deck.get("legend_rank", ""),
        "source_url": deck.get("source_url", ""),
    }
    payload["_normalized_format"] = mode
    payload["_deck"] = deck
    return payload


def payloads_from_decks(
    decks: List[Dict[str, Any]],
    *,
    limit: Optional[int] = None,
    include_seen: bool = False,
) -> List[Dict[str, Any]]:
    """Build publish payloads from parsed HSGuru decks using the production filter."""
    seen_data = load_seen()
    seen_codes = seen_data["codes"]
    max_items = HSGURU_PUBLISH_BATCH_LIMIT if limit is None else max(0, int(limit))

    payloads: List[Dict[str, Any]] = []
    for deck in decks:
        deck_code = deck.get("deck_code")
        if not deck_code:
            continue
        if not include_seen and deck_code in seen_codes:
            continue
        if not is_publishable_deck(deck):
            continue
        payloads.append(_deck_to_payload(deck))
        if max_items and len(payloads) >= max_items:
            break
    return payloads


def payloads_from_html(
    html: str,
    *,
    limit: Optional[int] = None,
    include_seen: bool = False,
) -> List[Dict[str, Any]]:
    """Parse HSGuru streamer-decks HTML and return publishable payloads."""
    archetypes = load_archetypes()
    decks = parse_decks(html, archetypes)
    if not decks:
        return []
    return payloads_from_decks(decks, limit=limit, include_seen=include_seen)


def get_new_decks(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает все новые HSGuru-колоды для ленты: games > 20 и wins > losses."""
    started = time.time()
    requested_pages = sum(len(_streamer_page_urls(base_url)) for base_url in _candidate_urls())

    if HS_DATA_API_ENABLED and get_data_api_streamer_decks is not None:
        try:
            api_decks, api_stats = get_data_api_streamer_decks()
            archetypes = load_archetypes()
            for deck in api_decks:
                original_name = deck.get("deck_name_en") or deck.get("deck_name") or "Deck"
                deck["deck_name_source"] = original_name
                deck["deck_name"] = translate_deck_name(str(original_name), archetypes) or str(original_name)

            decks = dedupe_decks(api_decks)
            if not decks:
                raise HSDataAPIError("api returned no streamer decks")

            payloads = payloads_from_decks(decks, limit=limit)
            publishable_all = payloads_from_decks(decks, limit=0, include_seen=True)
            seen_data = load_seen()
            status = {
                "ok": True,
                "stage": "complete",
                "source": "hs_data_api",
                "api_stats": api_stats,
                "parsed_raw": len(api_decks),
                "parsed_unique": len(decks),
                "publishable_all": len(publishable_all),
                "publishable_new": len(payloads),
                "seen_codes": len(seen_data.get("codes", set())),
                "duration_sec": round(time.time() - started, 3),
            }
            _write_status(status)
            print(
                f"[HSGuru] source=hs_data_api parsed_unique={len(decks)} "
                f"publishable_all={len(publishable_all)} publishable_new={len(payloads)}"
            )
            return payloads
        except Exception as e:
            print(f"[HSGuru] HS Data API fallback to direct fetch: {e}")

    try:
        pages = fetch_streamer_pages()
    except Exception as e:
        print(f"[HSGuru] fetch error: {e}")
        _write_status({
            "ok": False,
            "stage": "fetch",
            "error": str(e)[:1000],
            "requested_pages": requested_pages,
            "duration_sec": round(time.time() - started, 3),
        })
        return []

    archetypes = load_archetypes()
    raw_decks: List[Dict[str, Any]] = []
    page_stats: List[Dict[str, Any]] = []
    for index, html in enumerate(pages, 1):
        page_decks = parse_decks(html, archetypes)
        raw_decks.extend(page_decks)
        page_stats.append({
            "page": index,
            "html_length": len(html),
            "parsed_decks": len(page_decks),
        })

    decks = dedupe_decks(raw_decks)
    if HSGURU_MIN_PARSED_DECKS and len(decks) < HSGURU_MIN_PARSED_DECKS:
        message = (
            f"parsed_unique={len(decks)} below minimum "
            f"{HSGURU_MIN_PARSED_DECKS}; parser output considered unsafe"
        )
        print(f"[HSGuru] {message}")
        _write_status({
            "ok": False,
            "stage": "parse",
            "error": message,
            "requested_pages": requested_pages,
            "pages_fetched": len(pages),
            "page_stats": page_stats,
            "parsed_raw": len(raw_decks),
            "parsed_unique": len(decks),
            "duration_sec": round(time.time() - started, 3),
        })
        return []

    payloads = payloads_from_decks(decks, limit=limit)
    publishable_all = payloads_from_decks(decks, limit=0, include_seen=True)
    seen_data = load_seen()
    status = {
        "ok": True,
        "stage": "complete",
        "requested_pages": requested_pages,
        "pages_fetched": len(pages),
        "page_stats": page_stats,
        "parsed_raw": len(raw_decks),
        "parsed_unique": len(decks),
        "publishable_all": len(publishable_all),
        "publishable_new": len(payloads),
        "seen_codes": len(seen_data.get("codes", set())),
        "duration_sec": round(time.time() - started, 3),
    }
    _write_status(status)
    print(
        f"[HSGuru] pages={len(pages)} parsed_unique={len(decks)} "
        f"publishable_all={len(publishable_all)} publishable_new={len(payloads)}"
    )
    return payloads


def get_one_new_deck() -> Optional[Dict[str, Any]]:
    """
    Загружает HSGuru, находит первую новую колоду для ленты.
    Фильтр: строго больше 20 игр и wins > losses.
    """
    decks = get_new_decks(limit=1)
    return decks[0] if decks else None


def mark_deck_published(payload: Dict[str, Any]) -> None:
    """Сохранить колоду как опубликованную (вызвать после успешной публикации)."""
    with _exclusive_lock(_lock_path()):
        seen_data = load_seen()
        code = payload["deck_code"]
        seen_data["codes"].add(code)
        norm = payload.get("_normalized_format", "")
        seen_data["decks"][code] = {
            "published_at": datetime.now().isoformat(),
            "format": norm,
        }
        seen_data["last_published_format"] = norm
        save_seen(seen_data)
