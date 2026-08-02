"""
WordPress REST API Integration for deck publishing.
Adapted from tg-manacost-bot; reads config from Deckview config.
"""
from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.parse
import urllib.request

from config import (
    WP_APP_PASSWORD,
    WP_BASE_URL,
    WP_UPLOAD_ENABLED,
    WP_USER,
)


class WordPressClient:
    """WordPress REST API client."""

    def __init__(self):
        self.base_url = WP_BASE_URL
        self.user = WP_USER
        self.password = WP_APP_PASSWORD
        self.enabled = WP_UPLOAD_ENABLED
        self._taxonomy_cache: Dict[str, List[Dict]] = {}

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
        url = f"{self.base_url}{endpoint}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", auth)
        req.add_header("User-Agent", "Deckview/1.0")
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return {"success": True, "data": json.loads(body) if body else {}, "error": None, "status": resp.status}
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}", "data": json.loads(error_body) if error_body else None}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"URL Error: {e.reason}", "data": None}
        except Exception as e:
            return {"success": False, "error": str(e), "data": None}

    def get_taxonomy_terms(self, taxonomy: str, force_refresh: bool = False) -> List[Dict]:
        if taxonomy in self._taxonomy_cache and not force_refresh:
            return self._taxonomy_cache[taxonomy]
        result = self._request("GET", f"/wp-json/wp/v2/{taxonomy}?per_page=100")
        if result["success"] and isinstance(result["data"], list):
            self._taxonomy_cache[taxonomy] = result["data"]
            return result["data"]
        return []

    def find_term_id(self, taxonomy: str, term_name: str) -> Optional[int]:
        if not term_name:
            return None
        terms = self.get_taxonomy_terms(taxonomy)
        term_name_lower = term_name.strip().lower()
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
            return {"success": True, "media_id": media_data.get("id"), "url": media_data.get("source_url"), "data": media_data}
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
        tags_string = ", ".join(tag_list)
        class_id = self.find_term_id("deck_class", deck_class)
        mode_id = self.find_term_id("deck_mode", deck_mode)
        legend_val = int(legend_rank) if str(legend_rank).strip().isdigit() else ""
        post_data = {
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
        if mode_id:
            post_data["deck_mode"] = [mode_id]
        payload = json.dumps(post_data, ensure_ascii=False).encode("utf-8")
        result = self._request("POST", "/wp-json/wp/v2/hs_deck", data=payload, headers={"Content-Type": "application/json"})
        if not result["success"]:
            return result
        post_id = result["data"].get("id")
        payload_data = {
            "deck_code": post_data["meta"]["_deck_code"],
            "dust_cost": post_data["meta"]["_dust_cost"],
            "custom_tags": post_data["meta"]["_custom_tags"],
            "streamer": post_data["meta"]["_deck_streamer"],
            "player": post_data["meta"]["_deck_player"],
            "source_url": post_data["meta"]["_deck_source_url"],
            "wins": post_data["meta"]["_deck_wins"],
            "losses": post_data["meta"]["_deck_losses"],
            # Явно включаем колоду в ленту [hs_decks] (иначе meta_query плагина её скрывает).
            "hide_from_feed": "0",
        }
        if post_data["meta"]["_deck_peak"]:
            payload_data["peak"] = post_data["meta"]["_deck_peak"]
        if post_data["meta"]["_deck_latest"]:
            payload_data["latest"] = post_data["meta"]["_deck_latest"]
        if post_data["meta"]["_deck_worst"]:
            payload_data["worst"] = post_data["meta"]["_deck_worst"]
        if post_data["meta"]["_deck_legend_rank"] != "" and str(post_data["meta"]["_deck_legend_rank"]).strip().isdigit():
            payload_data["legend_rank"] = int(post_data["meta"]["_deck_legend_rank"])
        self._request("POST", f"/wp-json/manacost/v1/deck-meta/{post_id}", data=json.dumps(payload_data, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
        return {"success": True, "post_id": post_id, "url": result["data"].get("link"), "data": result["data"]}


_client: Optional[WordPressClient] = None


def get_client() -> WordPressClient:
    global _client
    if _client is None:
        _client = WordPressClient()
    return _client


def upload_deck_image(image_bytes: BytesIO, filename: str) -> Optional[str]:
    result = get_client().upload_media(image_bytes, filename)
    return result.get("url") if result["success"] else None


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
    if not get_client().enabled:
        return False
    media_id = None
    if image_bytes is not None:
        filename = f"deck-{hashlib.sha256(deck_code.encode('utf-8')).hexdigest()[:12]}.png"
        upload_result = get_client().upload_media(image_bytes, filename)
        if not upload_result["success"]:
            return False
        media_id = upload_result.get("media_id")
    post_result = get_client().create_deck_post(
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
    return post_result["success"]
