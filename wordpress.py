"""
WordPress REST API integration for Hearthstone deck publishing.
Handles media uploads, hs_deck post creation and Manacost custom meta writes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

import config


class WordPressClient:
    """WordPress REST API client with comprehensive error handling."""

    def __init__(self):
        self.base_url = config.WP_BASE_URL
        self.user = config.WP_USER
        self.password = config.WP_APP_PASSWORD
        self.enabled = config.WP_UPLOAD_ENABLED
        self._taxonomy_cache: Dict[str, List[Dict[str, Any]]] = {}

    def _auth_header(self) -> Optional[str]:
        if not (self.base_url and self.user and self.password):
            return None
        token = f"{self.user}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("utf-8")

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        auth = self._auth_header()
        if not auth:
            return {"success": False, "error": "WordPress credentials not configured", "data": None}

        req = urllib.request.Request(f"{self.base_url}{endpoint}", data=data, method=method)
        req.add_header("Authorization", auth)
        req.add_header("User-Agent", "Deckview/1.0")
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return {
                    "success": True,
                    "data": json.loads(body) if body else {},
                    "error": None,
                    "status": resp.status,
                }
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            return {
                "success": False,
                "error": f"HTTP {e.code}: {e.reason}",
                "data": json.loads(error_body) if error_body else None,
                "status": e.code,
            }
        except urllib.error.URLError as e:
            return {"success": False, "error": f"URL Error: {e.reason}", "data": None}
        except Exception as e:
            return {"success": False, "error": str(e), "data": None}

    def test_connection(self) -> Dict[str, Any]:
        result = self._request("GET", "/wp-json/wp/v2/users/me")
        if result["success"]:
            user_data = result["data"]
            return {
                "success": True,
                "user": user_data.get("name", "Unknown"),
                "user_id": user_data.get("id"),
                "roles": user_data.get("roles", []),
            }
        return result

    def get_taxonomy_terms(self, taxonomy: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if taxonomy in self._taxonomy_cache and not force_refresh:
            return self._taxonomy_cache[taxonomy]

        result = self._request("GET", f"/wp-json/wp/v2/{taxonomy}?per_page=100")
        if result["success"] and isinstance(result["data"], list):
            self._taxonomy_cache[taxonomy] = result["data"]
            return result["data"]
        return []

    def find_term_id(self, taxonomy: str, term_name: Optional[str]) -> Optional[int]:
        if not term_name:
            return None

        terms = self.get_taxonomy_terms(taxonomy)
        term_name_lower = str(term_name).strip().lower()
        for term in terms:
            if term.get("name", "").strip().lower() == term_name_lower:
                return term.get("id")
            if term.get("slug", "").strip().lower() == term_name_lower:
                return term.get("id")
        return None

    def upload_media(self, image_bytes: BytesIO, filename: str) -> Dict[str, Any]:
        result = self._request(
            "POST",
            "/wp-json/wp/v2/media",
            data=image_bytes.getvalue(),
            headers={
                "Content-Type": "image/png",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            timeout=120,
        )
        if result["success"]:
            media_data = result["data"]
            return {
                "success": True,
                "media_id": media_data.get("id"),
                "url": media_data.get("source_url"),
                "data": media_data,
            }
        return result

    def create_deck_post(
        self,
        title: str,
        deck_code: str,
        dust_cost: int,
        deck_class: Optional[str] = None,
        deck_mode: Optional[str] = None,
        streamer: Optional[str] = None,
        player: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_url: Optional[str] = None,
        media_id: Optional[int] = None,
        wins: int = 0,
        losses: int = 0,
        peak: str = "",
        latest: str = "",
        worst: str = "",
        legend_rank: str = "",
    ) -> Dict[str, Any]:
        tag_list = []
        if streamer:
            tag_list.append(streamer)
        if player and player != streamer:
            tag_list.append(player)
        if tags:
            tag_list.extend(tags)
        tags_string = ", ".join(tag_list)

        class_id = self.find_term_id("deck_class", deck_class)
        mode_id = self.find_term_id("deck_mode", deck_mode)
        legend_val = int(legend_rank) if str(legend_rank).strip().isdigit() else ""

        post_data: Dict[str, Any] = {
            "title": title,
            "status": "publish",
            "meta": {
                "_deck_code": deck_code,
                "_dust_cost": int(dust_cost) if dust_cost else 0,
                "_custom_tags": tags_string,
                "_deck_streamer": streamer or "",
                "_deck_player": player or streamer or "",
                "_deck_source_url": source_url or "",
                "_deck_wins": int(wins) if (wins is not None and wins != "") else 0,
                "_deck_losses": int(losses) if (losses is not None and losses != "") else 0,
                "_deck_peak": str(peak) if peak else "",
                "_deck_latest": str(latest) if latest else "",
                "_deck_worst": str(worst) if worst else "",
                "_deck_legend_rank": legend_val,
            },
        }

        if media_id:
            post_data["featured_media"] = media_id
        if class_id:
            post_data["deck_class"] = [class_id]
        elif deck_class:
            print(f"   [WARN] deck_class term not found in WordPress: {deck_class}")
        if mode_id:
            post_data["deck_mode"] = [mode_id]
        elif deck_mode:
            print(f"   [WARN] deck_mode term not found in WordPress: {deck_mode}")

        payload = json.dumps(post_data, ensure_ascii=False).encode("utf-8")
        result = self._request(
            "POST",
            "/wp-json/wp/v2/hs_deck",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        if not result["success"]:
            return result

        post_id = result["data"].get("id")
        update_result = self._update_post_meta(post_id, post_data["meta"])
        if not update_result["success"]:
            print(f"   [WARN] Post created but meta save failed: {update_result.get('error')}")

        return {
            "success": True,
            "post_id": post_id,
            "url": result["data"].get("link"),
            "data": result["data"],
        }

    def _update_post_meta(self, post_id: int, meta: Dict[str, Any]) -> Dict[str, Any]:
        payload_data: Dict[str, Any] = {
            "wins": 0,
            "losses": 0,
            # The [hs_decks] feed hides imported posts unless this is explicitly disabled.
            "hide_from_feed": "0",
        }

        if "_deck_code" in meta:
            payload_data["deck_code"] = meta["_deck_code"]
        if "_dust_cost" in meta:
            payload_data["dust_cost"] = meta["_dust_cost"]
        if "_custom_tags" in meta:
            payload_data["custom_tags"] = meta["_custom_tags"]
        if "_deck_streamer" in meta:
            payload_data["streamer"] = meta["_deck_streamer"]
        if "_deck_player" in meta:
            payload_data["player"] = meta["_deck_player"]
        if "_deck_source_url" in meta:
            payload_data["source_url"] = meta["_deck_source_url"]
        if "_deck_wins" in meta:
            payload_data["wins"] = int(meta["_deck_wins"] or 0)
        if "_deck_losses" in meta:
            payload_data["losses"] = int(meta["_deck_losses"] or 0)
        if meta.get("_deck_peak"):
            payload_data["peak"] = str(meta["_deck_peak"])
        if meta.get("_deck_latest"):
            payload_data["latest"] = str(meta["_deck_latest"])
        if meta.get("_deck_worst"):
            payload_data["worst"] = str(meta["_deck_worst"])
        if meta.get("_deck_legend_rank") != "":
            payload_data["legend_rank"] = int(meta["_deck_legend_rank"])

        return self._request(
            "POST",
            f"/wp-json/manacost/v1/deck-meta/{post_id}",
            data=json.dumps(payload_data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )


_client: Optional[WordPressClient] = None


def get_client() -> WordPressClient:
    global _client
    if _client is None:
        _client = WordPressClient()
    return _client


def _wp_auth_header() -> Optional[str]:
    return get_client()._auth_header()


def upload_deck_image(image_bytes: BytesIO, filename: str) -> Optional[str]:
    result = get_client().upload_media(image_bytes, filename)
    return result.get("url") if result["success"] else None


def upload_deck_media(image_bytes: BytesIO, filename: str) -> Optional[dict]:
    result = get_client().upload_media(image_bytes, filename)
    return result.get("data") if result["success"] else None


def _wp_request(method: str, path: str, data: Optional[bytes] = None,
                headers: Optional[dict] = None) -> Optional[dict]:
    result = get_client()._request(method, path, data, headers)
    return result.get("data") if result["success"] else None


def _get_term_id(taxonomy: str, name: Optional[str]) -> Optional[int]:
    return get_client().find_term_id(taxonomy, name)


def send_ingest_log(payload: dict) -> None:
    get_client()._request(
        "POST",
        "/wp-json/manacost/v1/ingest-log",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def create_hs_deck_post(
    deck_code: str,
    deck_name: str,
    streamer: Optional[str],
    player: Optional[str],
    dust_cost: int,
    source_url: Optional[str],
    image_bytes: Optional[BytesIO],
    deck_class: Optional[str] = None,
    deck_mode: Optional[str] = None,
    wins: int = 0,
    losses: int = 0,
    peak: str = "",
    latest: str = "",
    worst: str = "",
    legend_rank: str = "",
) -> bool:
    client = get_client()
    if not client.enabled:
        print("[SKIP] WP_UPLOAD_ENABLED=0")
        return False

    media_id = None
    if image_bytes is not None and not getattr(config, "WP_USE_KOLODAHS_IMAGE", False):
        filename = f"deck-{hashlib.sha256(deck_code.encode('utf-8')).hexdigest()[:12]}.png"
        upload_result = client.upload_media(image_bytes, filename)
        if not upload_result["success"]:
            print(f"[ERROR] Media upload failed: {upload_result.get('error')}")
            send_ingest_log({
                "status": "error",
                "deck_name": deck_name,
                "deck_code": deck_code[:20] + "...",
                "message": f"media upload failed: {upload_result.get('error')}",
            })
            return False
        media_id = upload_result.get("media_id")

    post_result = client.create_deck_post(
        title=deck_name,
        deck_code=deck_code,
        dust_cost=dust_cost,
        deck_class=deck_class,
        deck_mode=deck_mode,
        streamer=streamer,
        player=player,
        source_url=source_url,
        media_id=media_id,
        wins=wins,
        losses=losses,
        peak=peak,
        latest=latest,
        worst=worst,
        legend_rank=legend_rank,
    )

    if not post_result["success"]:
        send_ingest_log({
            "status": "error",
            "deck_name": deck_name,
            "deck_code": deck_code[:20] + "...",
            "message": f"post creation failed: {post_result.get('error')}",
        })
        return False

    send_ingest_log({
        "status": "success",
        "deck_name": deck_name,
        "deck_code": deck_code[:20] + "...",
        "post_id": post_result.get("post_id"),
        "message": "created successfully",
    })
    return True
