#!/usr/bin/env python3
"""
HSGuru Deck Parser - Автоматический парсер колод для WordPress
==============================================================

Парсит колоды с hsguru.com/streamer-decks и загружает на WordPress.
Запускается каждые 30 минут и проверяет новые колоды.

Использование:
    python deck_parser.py              # Запуск в режиме демона (каждые 30 мин)
    python deck_parser.py --once 5     # Однократный запуск, 5 колод
    python deck_parser.py --daemon     # Явный запуск демона
"""
import asyncio
import sys
import json
import hashlib
import base64
import urllib.request
import urllib.error
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import aiohttp
from bs4 import BeautifulSoup

# Hearthstone deck parsing
import hearthstone.deckstrings as deckstrings

# ============================================================================
# CONFIGURATION
# ============================================================================

# Paths
BASE_DIR = Path(__file__).parent
CARDS_JSON = BASE_DIR / "cards.json"
CARDS_RU_JSON = BASE_DIR / "cardsRU.json"
CARDS_IMAGES = BASE_DIR / "cards"
SEEN_FILE = BASE_DIR / "seen_decks.json"
ARCHETYPES_CSV = BASE_DIR / "Архетипы.csv"

# HSGuru
HSGURU_URL = "https://www.hsguru.com/streamer-decks"

# Interval (30 minutes)
CHECK_INTERVAL = 30 * 60

# Dust costs by rarity
DUST_COSTS = {
    "COMMON": 40,
    "RARE": 100,
    "EPIC": 400,
    "LEGENDARY": 1600,
}

# Hero DBF ID to class name (Russian for WordPress)
HERO_CLASS_MAP = {
    274: "Друид", 7: "Воин", 31: "Охотник", 637: "Маг",
    671: "Паладин", 813: "Жрец", 893: "Чернокнижник",
    930: "Разбойник", 1066: "Шаман", 56550: "Охотник на демонов",
    78065: "Рыцарь смерти",
    # Alternative skins
    2826: "Воин", 2827: "Охотник", 2828: "Маг", 2829: "Шаман",
    40195: "Жрец", 40183: "Паладин", 40323: "Чернокнижник",
    57761: "Разбойник", 60224: "Друид", 74481: "Охотник на демонов",
}

# Format mapping (English to Russian)
FORMAT_MAP = {
    "Standard": "Стандарт",
    "Wild": "Вольный", 
    "Classic": "Классический",
    "Twist": "Потасовка",
}


# ============================================================================
# ARCHETYPE TRANSLATION
# ============================================================================

def load_archetypes() -> Dict[str, str]:
    """Load archetype translations from CSV file (English -> Russian)."""
    translations = {}
    
    if not ARCHETYPES_CSV.exists():
        print(f"[WARN] Archetypes file not found: {ARCHETYPES_CSV}")
        return translations
    
    try:
        with open(ARCHETYPES_CSV, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and headers
                if not line or line.startswith(",,") or "Англ. названия" in line:
                    continue
                
                parts = line.split(",")
                if len(parts) >= 3:
                    # Column B = English, Column C = Russian
                    eng_name = parts[1].strip().strip('"')
                    rus_name = parts[2].strip().strip('"')
                    
                    if eng_name and rus_name:
                        # Store with lowercase key for case-insensitive matching
                        translations[eng_name.lower()] = rus_name
        
        print(f"[OK] Loaded {len(translations)} archetype translations")
    except Exception as e:
        print(f"[ERROR] Failed to load archetypes: {e}")
    
    return translations


def translate_deck_name(name: str, archetypes: Dict[str, str]) -> str:
    """Translate deck name from English to Russian using archetype table."""
    if not name or not archetypes:
        return name
    
    name_lower = name.lower().strip()
    
    # Try exact match first
    if name_lower in archetypes:
        return archetypes[name_lower]
    
    # Try partial match - find longest matching archetype
    best_match = None
    best_length = 0
    
    for eng, rus in archetypes.items():
        if eng in name_lower and len(eng) > best_length:
            best_match = rus
            best_length = len(eng)
    
    if best_match:
        return best_match
    
    # No translation found - return original
    return name


# Global archetypes (loaded once)
ARCHETYPES: Dict[str, str] = {}


# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def load_config():
    """Load configuration from .env file."""
    env_file = BASE_DIR / ".env"
    config = {
        "WP_BASE_URL": "",
        "WP_USER": "",
        "WP_APP_PASSWORD": "",
        "WP_UPLOAD_ENABLED": True,
    }
    
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key in config:
                        if key == "WP_UPLOAD_ENABLED":
                            config[key] = value.lower() not in ("0", "false", "no")
                        else:
                            config[key] = value
    
    return config


CONFIG = load_config()


# ============================================================================
# CARD DATABASE
# ============================================================================

class CardDB:
    """Simple card database for dust calculation and image generation."""
    
    def __init__(self):
        self.cards: Dict[int, Dict] = {}
        self.load()
    
    def load(self):
        if not CARDS_JSON.exists():
            print(f"[ERROR] Card database not found: {CARDS_JSON}")
            return
        
        with open(CARDS_JSON, "r", encoding="utf-8") as f:
            cards_data = json.load(f)
        
        for card in cards_data:
            dbf_id = card.get("dbfId")
            if dbf_id:
                self.cards[dbf_id] = {
                    "id": card.get("id", ""),
                    "name": card.get("name", ""),
                    "cost": card.get("cost", 0),
                    "rarity": card.get("rarity", ""),
                    "card_class": card.get("cardClass", ""),
                }
        
        # Load Russian names
        if CARDS_RU_JSON.exists():
            with open(CARDS_RU_JSON, "r", encoding="utf-8") as f:
                ru_data = json.load(f)
            for card in ru_data:
                dbf_id = card.get("dbfId")
                if dbf_id in self.cards and card.get("name"):
                    self.cards[dbf_id]["name_ru"] = card["name"]
        
        print(f"[OK] Loaded {len(self.cards)} cards")
    
    def get(self, dbf_id: int) -> Optional[Dict]:
        return self.cards.get(dbf_id)


# ============================================================================
# WORDPRESS CLIENT
# ============================================================================

class WordPressClient:
    """WordPress REST API client."""
    
    def __init__(self):
        self.base_url = CONFIG["WP_BASE_URL"]
        self.user = CONFIG["WP_USER"]
        self.password = CONFIG["WP_APP_PASSWORD"]
        self._taxonomy_cache: Dict[str, List[Dict]] = {}
    
    def _auth_header(self) -> Optional[str]:
        if not (self.base_url and self.user and self.password):
            return None
        token = f"{self.user}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("utf-8")
    
    def _request(self, method: str, endpoint: str, data: bytes = None, 
                 headers: Dict = None, timeout: int = 60) -> Dict:
        auth = self._auth_header()
        if not auth:
            return {"success": False, "error": "WordPress not configured"}
        
        url = f"{self.base_url}{endpoint}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", auth)
        req.add_header("User-Agent", "DeckParser/1.0")
        
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return {"success": True, "data": json.loads(body) if body else {}}
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            return {"success": False, "error": f"HTTP {e.code}", "data": json.loads(error_body) if error_body else None}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def test_connection(self) -> bool:
        result = self._request("GET", "/wp-json/wp/v2/users/me")
        if result["success"]:
            print(f"[OK] WordPress: {result['data'].get('name', 'Unknown')}")
            return True
        print(f"[ERROR] WordPress connection failed: {result.get('error')}")
        return False
    
    def get_taxonomy_terms(self, taxonomy: str) -> List[Dict]:
        if taxonomy in self._taxonomy_cache:
            return self._taxonomy_cache[taxonomy]
        result = self._request("GET", f"/wp-json/wp/v2/{taxonomy}?per_page=100")
        if result["success"] and isinstance(result["data"], list):
            self._taxonomy_cache[taxonomy] = result["data"]
            return result["data"]
        return []
    
    def find_term_id(self, taxonomy: str, name: str) -> Optional[int]:
        if not name:
            return None
        for term in self.get_taxonomy_terms(taxonomy):
            if term.get("name", "").lower() == name.lower():
                return term.get("id")
        return None
    
    def upload_media(self, image_bytes: BytesIO, filename: str) -> Optional[int]:
        result = self._request(
            "POST", "/wp-json/wp/v2/media",
            data=image_bytes.getvalue(),
            headers={
                "Content-Type": "image/png",
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
            timeout=120
        )
        if result["success"]:
            return result["data"].get("id")
        print(f"   [ERROR] Media upload failed: {result.get('error')}")
        return None
    
    def create_deck(self, title: str, deck_code: str, dust_cost: int,
                    deck_class: str, deck_mode: str, streamer: str,
                    media_id: int) -> Optional[int]:
        """Create deck post and set meta via custom endpoint."""
        
        class_id = self.find_term_id("deck_class", deck_class)
        mode_id = self.find_term_id("deck_mode", deck_mode)
        
        # Create post
        post_data = {
            "title": title,
            "status": "publish",
            "featured_media": media_id,
        }
        if class_id:
            post_data["deck_class"] = [class_id]
        if mode_id:
            post_data["deck_mode"] = [mode_id]
        
        result = self._request(
            "POST", "/wp-json/wp/v2/hs_deck",
            data=json.dumps(post_data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        if not result["success"]:
            print(f"   [ERROR] Post creation failed: {result.get('error')}")
            return None
        
        post_id = result["data"].get("id")
        print(f"   [OK] Post created: ID={post_id}")
        
        # Set meta via custom endpoint
        meta_data = {
            "deck_code": deck_code,
            "dust_cost": dust_cost,
            "custom_tags": streamer,
            "streamer": streamer,
            "player": streamer,
        }
        
        meta_result = self._request(
            "POST", f"/wp-json/manacost/v1/deck-meta/{post_id}",
            data=json.dumps(meta_data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        if meta_result["success"]:
            print(f"   [OK] Meta saved")
        else:
            print(f"   [WARN] Meta save failed: {meta_result.get('error')}")
        
        return post_id


# ============================================================================
# DECK PARSING
# ============================================================================

async def fetch_html(url: str) -> str:
    """Fetch HTML from URL."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            return await resp.text()


def parse_decks(html: str, card_db: CardDB, archetypes: Dict[str, str]) -> List[Dict]:
    """Parse decks from HSGuru HTML with archetype translation."""
    soup = BeautifulSoup(html, "html.parser")
    decks = []
    
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        
        try:
            # Find deck name from link to /deck/...
            deck_name_en = ""
            deck_link = row.select_one('a[href^="/deck/"]')
            if deck_link:
                deck_name_en = deck_link.get_text(strip=True)
            
            # Translate to Russian using archetype table
            deck_name = translate_deck_name(deck_name_en, archetypes)
            
            # Streamer is in the second link or cell
            streamer = cells[1].get_text(strip=True)
            
            # Format is in cell 2
            format_cell = cells[2].get_text(strip=True)
            
            # Get deck code from data-clipboard-text attribute
            clip_elem = row.select_one("[data-clipboard-text]")
            deck_code = ""
            if clip_elem:
                deck_code = clip_elem.get("data-clipboard-text", "")
            
            if not deck_code or not deck_name:
                continue
            
            # Calculate dust cost
            dust_cost = 0
            try:
                deck_parts = deckstrings.parse_deckstring(deck_code)
                cards = deck_parts[0]
                for dbf_id, count in cards:
                    card = card_db.get(dbf_id)
                    if card and card.get("rarity"):
                        dust_cost += DUST_COSTS.get(card["rarity"], 0) * count
            except:
                pass
            
            # Determine class
            deck_class = "Unknown"
            try:
                deck_parts = deckstrings.parse_deckstring(deck_code)
                heroes = deck_parts[1]
                if heroes:
                    deck_class = HERO_CLASS_MAP.get(heroes[0], "Unknown")
            except:
                pass
            
            # Convert format
            deck_mode = FORMAT_MAP.get(format_cell, "Стандарт")
            
            decks.append({
                "deck_name": deck_name,
                "streamer": streamer,
                "deck_code": deck_code,
                "dust_cost": dust_cost,
                "deck_class": deck_class,
                "deck_mode": deck_mode,
                "format": format_cell,
            })
        except Exception as e:
            continue
    
    return decks


# ============================================================================
# SEEN DECKS TRACKING
# ============================================================================

def load_seen() -> set:
    """Load seen deck codes."""
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except:
            pass
    return set()


def save_seen(seen: set):
    """Save seen deck codes."""
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


# ============================================================================
# IMAGE GENERATION (SIMPLIFIED)
# ============================================================================

def generate_deck_image(deck_code: str, card_db: CardDB) -> Optional[BytesIO]:
    """Generate deck image using the existing generator."""
    try:
        # Import the full generator
        from loader import CardDatabase
        from generator import DeckImageGenerator
        
        # Load card database
        full_card_db = CardDatabase(CARDS_JSON, CARDS_RU_JSON if CARDS_RU_JSON.exists() else None)
        generator = DeckImageGenerator(full_card_db, CARDS_IMAGES)
        
        # Decode deck
        deck_parts = deckstrings.parse_deckstring(deck_code)
        deck_cards = deck_parts[0]
        heroes = deck_parts[1]
        deck_format = deck_parts[2] if len(deck_parts) > 2 else None
        
        # Convert sideboards
        raw_sideboards = deck_parts[3] if len(deck_parts) > 3 else []
        sideboards = {}
        for item in raw_sideboards:
            if len(item) >= 3:
                card_id, count, owner_id = item[0], item[1], item[2]
                if owner_id not in sideboards:
                    sideboards[owner_id] = []
                sideboards[owner_id].append((card_id, count))
        
        hero_dbf_id = heroes[0] if heroes else None
        
        # Generate
        result = generator.generate_deck_image(
            deck_cards=deck_cards,
            sideboards=sideboards if sideboards else None,
            deck_format=deck_format,
            hero_dbf_id=hero_dbf_id
        )
        
        if isinstance(result, tuple):
            return result[0]
        return result
    except Exception as e:
        print(f"   [ERROR] Image generation failed: {e}")
        return None


# ============================================================================
# MAIN PARSER LOGIC
# ============================================================================

async def check_and_upload(limit: int = 5):
    """Check for new decks and upload them."""
    print(f"\n{'='*60}")
    print(f" Deck Parser - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    # Check WordPress
    if not CONFIG["WP_BASE_URL"]:
        print("[ERROR] WP_BASE_URL not configured in .env")
        return
    
    wp = WordPressClient()
    if not wp.test_connection():
        return
    
    # Load card database
    card_db = CardDB()
    if not card_db.cards:
        return
    
    # Load archetype translations
    archetypes = load_archetypes()
    
    # Load seen decks
    seen = load_seen()
    print(f"[INFO] Previously seen: {len(seen)} decks")
    
    # Fetch and parse
    print(f"[*] Fetching {HSGURU_URL}...")
    try:
        html = await fetch_html(HSGURU_URL)
        decks = parse_decks(html, card_db, archetypes)
        print(f"[OK] Found {len(decks)} decks")
    except Exception as e:
        print(f"[ERROR] Failed to fetch: {e}")
        return
    
    # Filter new decks
    new_decks = [d for d in decks if d["deck_code"] not in seen]
    print(f"[INFO] New decks: {len(new_decks)}")
    
    if not new_decks:
        print("[INFO] No new decks to upload")
        return
    
    # Upload
    success = 0
    for i, deck in enumerate(new_decks[:limit], 1):
        print(f"\n[{i}/{min(limit, len(new_decks))}] {deck['deck_name']}")
        print(f"   Streamer: {deck['streamer']}")
        print(f"   Dust: {deck['dust_cost']}")
        print(f"   Class: {deck['deck_class']}")
        
        # Generate image
        print("   [*] Generating image...")
        image = generate_deck_image(deck["deck_code"], card_db)
        if not image:
            continue
        print(f"   [OK] Image: {len(image.getvalue())} bytes")
        
        # Upload media
        print("   [*] Uploading...")
        filename = f"deck-{hashlib.sha256(deck['deck_code'].encode()).hexdigest()[:12]}.png"
        media_id = wp.upload_media(image, filename)
        if not media_id:
            continue
        
        # Create post
        post_id = wp.create_deck(
            title=deck["deck_name"],
            deck_code=deck["deck_code"],
            dust_cost=deck["dust_cost"],
            deck_class=deck["deck_class"],
            deck_mode=deck["deck_mode"],
            streamer=deck["streamer"],
            media_id=media_id,
        )
        
        if post_id:
            seen.add(deck["deck_code"])
            success += 1
    
    # Save seen
    save_seen(seen)
    
    print(f"\n{'='*60}")
    print(f" Done: {success}/{min(limit, len(new_decks))} uploaded")
    print(f"{'='*60}")


async def daemon_loop():
    """Run parser in daemon mode (every 30 minutes)."""
    print("="*60)
    print(" HSGuru Deck Parser - DAEMON MODE")
    print(f" Checking every {CHECK_INTERVAL // 60} minutes")
    print("="*60)
    
    while True:
        try:
            await check_and_upload(limit=10)
        except Exception as e:
            print(f"[ERROR] {e}")
        
        print(f"\n[SLEEP] Next check in {CHECK_INTERVAL // 60} minutes...")
        await asyncio.sleep(CHECK_INTERVAL)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="HSGuru Deck Parser")
    parser.add_argument("--once", type=int, metavar="N", help="Run once, upload N decks")
    parser.add_argument("--daemon", action="store_true", help="Run in daemon mode")
    
    args = parser.parse_args()
    
    if args.once:
        asyncio.run(check_and_upload(limit=args.once))
    elif args.daemon or len(sys.argv) == 1:
        # Default: daemon mode
        asyncio.run(daemon_loop())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
