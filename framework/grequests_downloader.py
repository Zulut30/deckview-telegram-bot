import io
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image

from db.config import FOLDER
from framework.hearthstonejson_downloader import download_from_hearthstonejson
from framework.http_session import get_http_session
from framework.wiki_downloader import download_from_wiki

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
}

# Альтернативный источник артов: HearthstoneJSON Art API по CardID (только проверенные совпадения).
# CORE_CS3_027 = «Средоточие воли» (Priest), не Consumption — не используем.
SLUG_TO_ART_CARD_ID = {
    "127536-alexandros-mograine": "RLK_706",
}
ART_RENDER_URL = "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x"
ARENA_DOWNLOAD_WORKERS = max(
    1,
    min(16, os.cpu_count() or 4),
)
_local_arena_index_lock = threading.RLock()
_local_arena_index_root = None
_local_arena_index_mtime_ns = None
_local_arena_image_index = {}


def _card_cache_path(slug):
    return f"{FOLDER}{slug}.png"


def _arena_marker_path(slug):
    return f"{FOLDER}{slug}.arena-v1"


def _hsjson_marker_path(slug):
    return f"{FOLDER}{slug}.hsjson-v1"


def _has_cached_photo(slug):
    path = _card_cache_path(slug)
    return os.path.isfile(path) and os.path.getsize(path) > 100


def _has_arena_cached_photo(slug):
    return _has_cached_photo(slug) and os.path.isfile(_arena_marker_path(slug))


def _is_valid_hero_render(slug):
    """Validate that the cached asset is a complete vertical card render."""
    path = _card_cache_path(slug)
    try:
        with Image.open(path) as image:
            width, height = image.size
        return width >= 256 and height >= int(width * 1.45)
    except (OSError, ValueError):
        return False


def _has_hsjson_hero_render(slug):
    return (
        os.path.isfile(_hsjson_marker_path(slug))
        and _is_valid_hero_render(slug)
    )


def _mark_arena_photo(slug, source_path=None):
    try:
        marker_path = _arena_marker_path(slug)
        temporary = (
            f"{marker_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        with open(temporary, "w", encoding="utf-8") as marker:
            marker.write("arena.hs-manacost.ru\n")
            if source_path:
                marker.write(f"local={Path(source_path).name}\n")
        os.replace(temporary, marker_path)
        return True
    except OSError:
        return False


def _reset_local_arena_image_index():
    """Forget the process-local Arena file index (used by tests and reloads)."""
    global _local_arena_index_root
    global _local_arena_index_mtime_ns
    global _local_arena_image_index
    with _local_arena_index_lock:
        _local_arena_index_root = None
        _local_arena_index_mtime_ns = None
        _local_arena_image_index = {}


def _local_arena_image_root():
    raw = os.getenv("DECKVIEW_ARENA_CARD_IMAGE_DIR", "").strip()
    if not raw:
        return None
    root = Path(raw)
    return root if root.is_dir() else None


def _build_local_arena_image_index(root):
    """Index Arena's full-card WebP cache in one cheap directory scan."""
    index = {}
    try:
        entries = os.scandir(root)
    except OSError:
        return index
    with entries:
        for entry in entries:
            name = entry.name
            if not entry.is_file() or not name.endswith(".webp"):
                continue
            card_id, separator, suffix = name.partition("-full-")
            if not separator or not card_id or "placeholder" in suffix:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            revision_match = re.search(r"(?:^|_)v(\d+)(?:_|\.)", suffix)
            visual_revision = (
                int(revision_match.group(1)) if revision_match else 0
            )
            source_priority = 2 if "blizzard" in suffix else 1
            score = (visual_revision, source_priority, stat.st_mtime_ns, name)
            previous = index.get(card_id)
            if previous is None or score > previous[0]:
                index[card_id] = (score, Path(entry.path))
    return {card_id: value[1] for card_id, value in index.items()}


def preload_local_arena_image_index():
    """Build Arena's local CardID index once so forked workers can share it."""
    global _local_arena_index_root
    global _local_arena_index_mtime_ns
    global _local_arena_image_index
    root = _local_arena_image_root()
    if root is None:
        return 0
    try:
        root_mtime_ns = root.stat().st_mtime_ns
    except OSError:
        return 0
    root_key = str(root.resolve())
    with _local_arena_index_lock:
        if (
            _local_arena_index_root != root_key
            or _local_arena_index_mtime_ns != root_mtime_ns
        ):
            _local_arena_image_index = _build_local_arena_image_index(root)
            _local_arena_index_root = root_key
            _local_arena_index_mtime_ns = root_mtime_ns
        return len(_local_arena_image_index)


def _resolve_local_arena_image(card_id):
    """Return a local Arena full-card asset without an HTTP round trip."""
    if preload_local_arena_image_index() <= 0:
        return None
    with _local_arena_index_lock:
        source = _local_arena_image_index.get(str(card_id).strip())
    if source is None:
        return None
    try:
        # stat()/is_file() can succeed even when the service account cannot
        # read the image itself (for example a 0640 koloda:koloda asset).  A
        # symlink to such a file looks ready to the downloader, then Pillow
        # fails later and the renderer used to omit the card entirely.
        return (
            source
            if source.is_file()
            and source.stat().st_size > 100
            and os.access(source, os.R_OK)
            else None
        )
    except OSError:
        return None


def _install_local_arena_photo(slug, source_path):
    """Link Arena's local WebP atomically; Pillow detects format by content."""
    target = _card_cache_path(slug)
    temporary = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        if os.path.lexists(temporary):
            os.remove(temporary)
        os.symlink(os.path.abspath(source_path), temporary)
        os.replace(temporary, target)
        return _mark_arena_photo(slug, source_path)
    except OSError:
        # Some filesystems or deployments may forbid symlinks. Keep a safe
        # local fallback without changing the public cache filename.
        try:
            if os.path.lexists(temporary):
                os.remove(temporary)
            shutil.copyfile(source_path, temporary)
            if os.path.getsize(temporary) <= 100:
                return False
            os.replace(temporary, target)
            return _mark_arena_photo(slug, source_path)
        except OSError:
            return False
    finally:
        try:
            if os.path.lexists(temporary):
                os.remove(temporary)
        except FileNotFoundError:
            pass


def _use_local_arena_photo(card):
    """Ensure the card cache points at Arena's current local visual revision."""
    card_id = str(card.get("cardId") or "").strip()
    slug = str(card.get("slug") or "").strip()
    if not card_id or not slug:
        return False
    identifiers = []
    try:
        dbf_id = int(card.get("dbfId"))
        if dbf_id > 0:
            identifiers.append(str(dbf_id))
    except (TypeError, ValueError):
        pass
    if card_id not in identifiers:
        identifiers.append(card_id)
    source_path = next(
        (
            candidate
            for identifier in identifiers
            if (candidate := _resolve_local_arena_image(identifier)) is not None
        ),
        None,
    )
    if source_path is None:
        return False

    target = Path(_card_cache_path(slug))
    try:
        if (
            target.is_symlink()
            and target.resolve(strict=True) == source_path.resolve(strict=True)
            and target.stat().st_size > 100
        ):
            marker_path = Path(_arena_marker_path(slug))
            if not marker_path.is_file():
                return _mark_arena_photo(slug, source_path)
            return True
    except OSError:
        pass
    return _install_local_arena_photo(slug, source_path)


def _mark_hsjson_photo(slug):
    try:
        with open(_hsjson_marker_path(slug), "w", encoding="utf-8") as marker:
            marker.write("art.hearthstonejson.com\n")
        try:
            os.remove(_arena_marker_path(slug))
        except FileNotFoundError:
            pass
        return True
    except OSError:
        return False


def _is_collectible_hero_card(card):
    card_type = str(
        card.get("deckviewCardType") or card.get("type") or ""
    ).strip().upper()
    try:
        card_type_id = int(card.get("cardTypeId"))
    except (TypeError, ValueError):
        card_type_id = 0
    return (card_type == "HERO" or card_type_id == 3) and card.get(
        "collectible"
    ) is not False


def _download_hero_render(card):
    """Cache a full Hero card locally instead of Arena's portrait asset."""
    slug = str(card.get("slug") or "").strip()
    card_id = card.get("cardId") or card.get("id") or card.get("dbfId")
    if not slug or card_id is None:
        return False
    if _has_hsjson_hero_render(slug):
        return True
    if not download_from_hearthstonejson(card_id, slug):
        return False
    if not _is_valid_hero_render(slug):
        print(f"HearthstoneJSON returned an incomplete Hero render for {slug}")
        return False
    return _mark_hsjson_photo(slug)


def _download_hero_batch(cards):
    unique_cards = {}
    for card in cards:
        slug = str(card.get("slug") or "").strip()
        if slug and slug not in unique_cards:
            unique_cards[slug] = card
    pending = list(unique_cards.values())
    if not pending:
        return set()
    workers = min(ARENA_DOWNLOAD_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(_download_hero_render, pending)
        return {
            card["slug"]
            for card, succeeded in zip(pending, results)
            if succeeded
        }


def _reuse_sideboard_arena_photo(slug):
    if not slug.endswith("-side"):
        return False
    base_slug = slug[:-5]
    if not _has_arena_cached_photo(base_slug):
        return False
    try:
        shutil.copyfile(_card_cache_path(base_slug), _card_cache_path(slug))
        shutil.copyfile(_arena_marker_path(base_slug), _arena_marker_path(slug))
        return True
    except OSError:
        return False


def _reuse_sideboard_photo(slug):
    if not slug.endswith("-side"):
        return False
    base_slug = slug[:-5]
    if not _has_cached_photo(base_slug):
        return False
    try:
        shutil.copyfile(_card_cache_path(base_slug), _card_cache_path(slug))
        return True
    except OSError:
        return False


def _save_image_bytes_to_png(slug, content, content_type=""):
    """Сохранить байты изображения (PNG или JPEG) как PNG."""
    try:
        img = Image.open(io.BytesIO(content)).convert("RGBA")
        img.save(f"{FOLDER}{slug}.png", "PNG")
        return True
    except Exception as e:
        print(f"Save image to PNG failed for {slug}: {e}")
        return False


def _download_from_art_api(slug):
    """Скачать арт из HearthstoneJSON Art API по CardID (для неизвестных карт)."""
    art_id = SLUG_TO_ART_CARD_ID.get(slug)
    if not art_id:
        return False
    url = f"{ART_RENDER_URL}/{art_id}.png"
    try:
        resp = get_http_session().get(url, headers=HEADERS, timeout=15)
        if resp.ok and resp.content and len(resp.content) > 500:
            return _save_image_bytes_to_png(slug, resp.content, resp.headers.get("Content-Type", ""))
    except Exception as e:
        print(f"Art API download failed for {slug}: {e}")
    return False


def _download_from_arena(card):
    """Prefer the authenticated Arena full-card image for deck rendering."""
    card_id = str(card.get("cardId") or "").strip()
    slug = str(card.get("slug") or "").strip()
    if not card_id or not slug:
        return False
    if _use_local_arena_photo(card):
        return True
    try:
        from manacost_api import get_card_image

        # Arena's background prewarmer stores canonical images by dbfId.
        # Ask for that identifier first so a CardID alias never triggers a
        # duplicate cold download of an already prepared public image.
        identifiers = []
        try:
            dbf_id = int(card.get("dbfId"))
            if dbf_id > 0:
                identifiers.append(str(dbf_id))
        except (TypeError, ValueError):
            pass
        if card_id not in identifiers:
            identifiers.append(card_id)

        for identifier in identifiers:
            try:
                content = get_card_image(identifier, "full")
            except Exception:
                continue
            if len(content) <= 100:
                continue
            if not _save_image_bytes_to_png(slug, content, "image/webp"):
                continue
            _mark_arena_photo(slug)
            return True
        return False
    except Exception as exc:
        print(
            f"Arena card image fallback for {slug}: "
            f"{type(exc).__name__}"
        )
        return False


def _download_arena_batch(cards):
    """Download unique Arena images concurrently and return successful slugs."""
    unique_cards = {}
    for card in cards:
        slug = str(card.get("slug") or "").strip()
        card_id = str(card.get("cardId") or "").strip()
        if slug and card_id and slug not in unique_cards:
            unique_cards[slug] = card

    pending = list(unique_cards.values())
    if not pending:
        return set()

    workers = min(ARENA_DOWNLOAD_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(_download_from_arena, pending)
        return {
            card["slug"]
            for card, succeeded in zip(pending, results)
            if succeeded
        }


class GRequestsDownloader:
    """Download card images (uses requests to avoid gevent/ssl recursion with aiogram)."""

    def save_photo(self, slug, response, name):
        is_valid = (
            response is not None
            and response.status_code == 200
            and response.content
            and len(response.content) > 100
            and response.content[:5] != b"<?xml"
        )
        if is_valid:
            ct = getattr(response, "headers", {}).get("Content-Type", "")
            if "image" in ct:
                return _save_image_bytes_to_png(slug, response.content, ct)
            with open(f"{FOLDER}{slug}.png", "wb") as photo:
                photo.write(response.content)
            return True
        print(f"Blizzard API failed for {slug} (status {getattr(response, 'status_code', None)}). Trying alternatives...")
        if download_from_wiki(slug, name):
            return True
        card_id = slug.split('-')[0] if '-' in slug else slug
        if download_from_hearthstonejson(card_id, slug):
            return True
        return _download_from_art_api(slug)

    def process_cards(self, cards):
        session = get_http_session()
        unique_cards = {}
        for card in cards:
            slug = str(card.get("slug") or "").strip()
            if slug and slug not in unique_cards:
                unique_cards[slug] = card
        cards = list(unique_cards.values())

        # Arena serves portrait-only assets for collectible Hero cards. Cache
        # complete localized card renders from HearthstoneJSON once and reuse
        # them from disk on every subsequent deck generation.
        hero_cards = [card for card in cards if _is_collectible_hero_card(card)]
        ready = {
            card["slug"]
            for card in hero_cards
            if _has_hsjson_hero_render(card["slug"])
        }
        ready.update(
            _download_hero_batch(
                [card for card in hero_cards if card["slug"] not in ready]
            )
        )

        regular_cards = [card for card in cards if not _is_collectible_hero_card(card)]
        arena_ready = set()
        primary_cards = [
            card for card in regular_cards if not card["slug"].endswith("-side")
        ]
        sideboard_cards = [
            card for card in regular_cards if card["slug"].endswith("-side")
        ]

        for card in primary_cards:
            slug = card["slug"]
            if _use_local_arena_photo(card) or _has_arena_cached_photo(slug):
                arena_ready.add(slug)
        arena_ready.update(
            _download_arena_batch(
                [card for card in primary_cards if card["slug"] not in arena_ready]
            )
        )

        # Sideboard copies can reuse a freshly downloaded main-deck image.
        for card in sideboard_cards:
            slug = card["slug"]
            if (
                _use_local_arena_photo(card)
                or _has_arena_cached_photo(slug)
                or _reuse_sideboard_arena_photo(slug)
            ):
                arena_ready.add(slug)
        arena_ready.update(
            _download_arena_batch(
                [card for card in sideboard_cards if card["slug"] not in arena_ready]
            )
        )
        ready.update(arena_ready)

        for card in cards:
            slug = card["slug"]
            if slug in ready:
                continue
            if not slug:
                continue
            if _has_arena_cached_photo(slug) or _reuse_sideboard_arena_photo(slug):
                continue
            if _has_cached_photo(slug) or _reuse_sideboard_photo(slug):
                continue
            try:
                resp = session.get(card["image"], headers=HEADERS, timeout=10)
                self.save_photo(slug, resp, card["name"])
            except Exception as e:
                print(f"Download error for {slug}: {e}")
                if not download_from_wiki(slug, card["name"]):
                    card_id = card.get("id") or (slug.split('-')[0] if '-' in slug else None)
                    if card_id:
                        download_from_hearthstonejson(card_id, slug)
