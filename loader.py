"""
Модуль для загрузки базы карт.
По умолчанию использует cards.json, но может загружать данные из Blizzard API.

Создает словарь для быстрого поиска карт по dbfId.
Также формирует индекс card_name_to_ids:
    английское имя карты -> список всех dbfId с таким именем.
Это нужно для случаев, когда одна и та же карта имеет несколько версий
(Legacy, Core и т.д.), но используется одно изображение.
"""
import json
from pathlib import Path
from typing import Dict, Optional, List
import config
import blizzard_api

# Глобальная мапа "английское имя карты -> список dbfId"
# Заполняется при инициализации CardDatabase и может использоваться
# другими модулями (например, generator.py) для поиска альтернативных id.
card_name_to_ids: Dict[str, List[int]] = {}


class CardDatabase:
    """Класс для работы с базой данных карт."""
    
    # dbfId для специальных карт (будут найдены при загрузке)
    ETC_BAND_MANAGER_DBFID: Optional[int] = None
    ZILLIAX_DELUXE_3000_DBFID: Optional[int] = None
    ZILLIAX_MODULE_DBFIDS: set = set()
    
    def __init__(self, json_path: Path, json_ru_path: Optional[Path] = None):
        """
        Инициализация базы данных карт.
        
        Args:
            json_path: Путь к файлу cards.json
            json_ru_path: Путь к файлу cardsRU.json с русскими названиями (опционально)
        """
        self.json_path = json_path
        self.json_ru_path = json_ru_path
        # Основная мапа dbfId -> данные карты
        self.cards: Dict[int, Dict] = {}
        # Локальная мапа "английское имя -> список dbfId"
        self.card_name_to_ids: Dict[str, List[int]] = {}
        # Исходные карты из источника (Blizzard API или JSON файл)
        self._raw_cards: List[Dict] = []
        self._metadata: Optional[Dict] = None
        self._cards_source: str = "local"
        self._local_id_map: Dict[int, str] = {}
        self.load_cards()
        self.load_russian_names()
        self.identify_special_cards()
    
    def _load_local_id_map(self) -> Dict[int, str]:
        if not self.json_path.exists():
            return {}
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        mapping: Dict[int, str] = {}
        for card in data:
            dbf_id = card.get("dbfId") or card.get("dbf_id")
            if isinstance(dbf_id, str) and dbf_id.isdigit():
                dbf_id = int(dbf_id)
            card_id = card.get("id")
            if isinstance(dbf_id, int) and isinstance(card_id, str) and card_id:
                mapping[dbf_id] = card_id
        return mapping

    def _resolve_card_id(self, card: Dict) -> str:
        card_id = card.get("cardId") or card.get("card_id")
        if card_id:
            return card_id
        raw_id = card.get("id")
        if isinstance(raw_id, str) and "_" in raw_id:
            return raw_id
        slug = card.get("slug")
        return slug or ""

    def _resolve_card_class(self, card: Dict) -> str:
        value = card.get("cardClass") or card.get("card_class")
        if value:
            return value
        class_id = card.get("classId") or card.get("class_id")
        if class_id and self._metadata and self._metadata.get("classes"):
            return self._metadata["classes"].get(class_id, "")
        return ""

    def _resolve_rarity(self, card: Dict) -> str:
        value = card.get("rarity") or card.get("rarity_name")
        if value:
            return value
        rarity_id = card.get("rarityId") or card.get("rarity_id")
        if rarity_id and self._metadata and self._metadata.get("rarities"):
            return self._metadata["rarities"].get(rarity_id, "")
        return ""

    def _resolve_type(self, card: Dict) -> str:
        value = card.get("type") or card.get("cardType")
        if value:
            return value
        type_id = card.get("cardTypeId") or card.get("type_id")
        if type_id and self._metadata and self._metadata.get("types"):
            return self._metadata["types"].get(type_id, "")
        return ""

    def load_cards(self) -> None:
        """
        Загружает карты из JSON файла и создает словарь dbfId -> данные карты.
        """
        cards_data: Optional[List[Dict]] = None
        metadata: Optional[Dict] = None

        if config.BLIZZARD_ENABLED and self.json_path.exists():
            self._local_id_map = self._load_local_id_map()

        if config.BLIZZARD_ENABLED and config.BLIZZARD_CLIENT_ID and config.BLIZZARD_CLIENT_SECRET:
            try:
                cards_data, metadata = blizzard_api.load_cards_from_blizzard(
                    client_id=config.BLIZZARD_CLIENT_ID,
                    client_secret=config.BLIZZARD_CLIENT_SECRET,
                    region=config.BLIZZARD_REGION,
                    locale=config.BLIZZARD_LOCALE,
                    cache_dir=config.BLIZZARD_CACHE_DIR,
                    cache_ttl_hours=config.BLIZZARD_CACHE_TTL_HOURS,
                    include_metadata=True,
                    collectible_only=config.BLIZZARD_COLLECTIBLE_ONLY,
                )
                self._cards_source = "blizzard"
            except Exception as e:
                print(f"Ошибка загрузки карт из Blizzard API: {e}")
                cards_data = None
                metadata = None

        if cards_data is None:
            if not self.json_path.exists():
                raise FileNotFoundError(f"Файл {self.json_path} не найден!")
            with open(self.json_path, "r", encoding="utf-8") as f:
                cards_data = json.load(f)
            self._cards_source = "local"

        self._raw_cards = cards_data or []
        self._metadata = metadata
        
        # Обнуляем глобальный и локальный индексы
        global card_name_to_ids
        card_name_to_ids.clear()
        self.card_name_to_ids.clear()
        
        # Создаем словарь для быстрого поиска по dbfId
        for card in self._raw_cards:
            dbf_id = card.get("dbfId") or card.get("dbf_id") or card.get("id")
            if isinstance(dbf_id, str) and dbf_id.isdigit():
                dbf_id = int(dbf_id)
            if dbf_id is not None:
                name_en = card.get("name", "Unknown")
                card_id = self._resolve_card_id(card)
                if isinstance(dbf_id, int) and self._local_id_map.get(dbf_id):
                    card_id = self._local_id_map[dbf_id]
                self.cards[dbf_id] = {
                    "id": card_id,
                    "cost": card.get("cost", card.get("manaCost", 0)),
                    "name": name_en,
                    "name_ru": card.get("name_ru", card.get("ru_name", card.get("name", "Unknown"))),
                    "rarity": self._resolve_rarity(card),
                    "type": self._resolve_type(card),
                    "card_class": self._resolve_card_class(card),
                    "collectible": card.get("collectible", True),
                    "image": card.get("image"),
                    "image_gold": card.get("imageGold") or card.get("image_gold"),
                }
                
                # Заполняем индекс "английское имя -> список dbfId"
                key = (name_en or "").strip()
                if key:
                    self.card_name_to_ids.setdefault(key, []).append(dbf_id)
        
        # Экспортируем локальный индекс в модульную глобальную мапу,
        # чтобы им могли пользоваться другие модули (например, generator.py).
        card_name_to_ids.update(self.card_name_to_ids)
        
        print(f"Загружено {len(self.cards)} карт из базы данных ({self._cards_source}).")
    
    def load_russian_names(self) -> None:
        """
        Загружает русские названия карт из cardsRU.json и обновляет существующие карты.
        """
        if config.BLIZZARD_ENABLED and config.BLIZZARD_CLIENT_ID and config.BLIZZARD_CLIENT_SECRET:
            try:
                cards_ru, _ = blizzard_api.load_cards_from_blizzard(
                    client_id=config.BLIZZARD_CLIENT_ID,
                    client_secret=config.BLIZZARD_CLIENT_SECRET,
                    region=config.BLIZZARD_REGION,
                    locale=config.BLIZZARD_LOCALE_RU,
                    cache_dir=config.BLIZZARD_CACHE_DIR,
                    cache_ttl_hours=config.BLIZZARD_CACHE_TTL_HOURS,
                    include_metadata=False,
                    collectible_only=config.BLIZZARD_COLLECTIBLE_ONLY,
                )
                ru_map = {}
                for card in cards_ru:
                    dbf_id = card.get("dbfId") or card.get("dbf_id") or card.get("id")
                    if isinstance(dbf_id, str) and dbf_id.isdigit():
                        dbf_id = int(dbf_id)
                    name_ru = card.get("name")
                    if dbf_id is not None and name_ru:
                        ru_map[dbf_id] = name_ru
                updated_count = 0
                for dbf_id, name_ru in ru_map.items():
                    if dbf_id in self.cards:
                        self.cards[dbf_id]["name_ru"] = name_ru
                        updated_count += 1
                print(f"Загружено {updated_count} русских названий карт (Blizzard API).")
                return
            except Exception as e:
                print(f"Ошибка загрузки русских названий из Blizzard API: {e}")

        if not self.json_ru_path or not self.json_ru_path.exists():
            print("Файл с русскими названиями не найден, пропускаем загрузку.")
            return
        
        try:
            with open(self.json_ru_path, 'r', encoding='utf-8') as f:
                cards_ru_data = json.load(f)
            
            updated_count = 0
            for card_ru in cards_ru_data:
                dbf_id = card_ru.get('dbfId')
                if dbf_id is not None and dbf_id in self.cards:
                    ru_name = card_ru.get('name', '')
                    if ru_name:
                        self.cards[dbf_id]['name_ru'] = ru_name
                        updated_count += 1
            
            print(f"Загружено {updated_count} русских названий карт.")
        except Exception as e:
            print(f"Ошибка при загрузке русских названий: {e}")
    
    def identify_special_cards(self) -> None:
        """
        Определяет dbfId для специальных карт: E.T.C., Band Manager и Zilliax Deluxe 3000.
        Также находит все модули Зиллакса.
        """
        cards_data = self._raw_cards
        if not cards_data and self.json_path.exists():
            with open(self.json_path, "r", encoding="utf-8") as f:
                cards_data = json.load(f)
        
        # Ищем E.T.C., Band Manager
        for card in cards_data:
            name = card.get('name', '')
            dbf_id = card.get("dbfId") or card.get("dbf_id") or card.get("id")
            if isinstance(dbf_id, str) and dbf_id.isdigit():
                dbf_id = int(dbf_id)
            
            if 'E.T.C.' in name and 'Band Manager' in name:
                self.ETC_BAND_MANAGER_DBFID = dbf_id
                print(f"Найден E.T.C., Band Manager: dbfId={dbf_id}")
            
            # Ищем Zilliax Deluxe 3000 (основная карта)
            if 'Zilliax Deluxe 3000' in name and dbf_id:
                # Проверяем, что это не модуль
                if not card.get("isZilliaxFunctionalModule") and not card.get("isZilliaxCosmeticModule"):
                    self.ZILLIAX_DELUXE_3000_DBFID = dbf_id
                    print(f"Найден Zilliax Deluxe 3000: dbfId={dbf_id}")
            
            # Ищем модули Зиллакса
            # В Blizzard API есть флаги isZilliaxFunctionalModule / isZilliaxCosmeticModule
            if dbf_id:
                if card.get("isZilliaxFunctionalModule") or card.get("isZilliaxCosmeticModule"):
                    self.ZILLIAX_MODULE_DBFIDS.add(dbf_id)
                    print(f"  Найден модуль Зиллакса: {name} (dbfId={dbf_id})")
                    continue
                # Fallback для JSON источника (старые поля)
                if 'Zilliax' in name:
                    # Пропускаем основную карту Zilliax Deluxe 3000
                    if self.ZILLIAX_DELUXE_3000_DBFID and dbf_id == self.ZILLIAX_DELUXE_3000_DBFID:
                        continue
                    text = card.get('text', '').lower()
                    if ('Module' in name or
                        'module' in text or
                        'zilliax' in text and ('attach' in text or 'module' in text)):
                        self.ZILLIAX_MODULE_DBFIDS.add(dbf_id)
                        print(f"  Найден модуль Зиллакса: {name} (dbfId={dbf_id})")
        
        if self.ZILLIAX_MODULE_DBFIDS:
            print(f"Найдено модулей Зиллакса: {len(self.ZILLIAX_MODULE_DBFIDS)}")
    
    def is_etc_card(self, dbf_id: int) -> bool:
        """Проверяет, является ли карта E.T.C., Band Manager."""
        return dbf_id == self.ETC_BAND_MANAGER_DBFID
    
    def is_zilliax_main(self, dbf_id: int) -> bool:
        """Проверяет, является ли карта основным Zilliax Deluxe 3000."""
        return dbf_id == self.ZILLIAX_DELUXE_3000_DBFID
    
    def is_zilliax_module(self, dbf_id: int) -> bool:
        """Проверяет, является ли карта модулем Зиллакса."""
        return dbf_id in self.ZILLIAX_MODULE_DBFIDS
    
    def get_card(self, dbf_id: int) -> Optional[Dict]:
        """
        Получает данные карты по dbfId.
        
        Args:
            dbf_id: Числовой идентификатор карты в базе данных
            
        Returns:
            Словарь с данными карты или None, если карта не найдена
        """
        return self.cards.get(dbf_id)
    
    def get_card_filename(self, dbf_id: int) -> Optional[str]:
        """
        Получает имя файла изображения карты по dbfId.
        
        Args:
            dbf_id: Числовой идентификатор карты в базе данных
            
        Returns:
            Имя файла (например, "SW_001.png") или None, если карта не найдена
        """
        card = self.get_card(dbf_id)
        if card:
            card_id = (card.get("id") or "").strip()
            if not card_id:
                return None
            return f"{card_id}.png"
        return None
    
    def search_card_by_name(self, query_name: str) -> Optional[Dict]:
        """
        Ищет карту по имени (регистронезависимый поиск подстроки).
        Поддерживает поиск по английским и русским названиям.
        Приоритет отдается точным совпадениям.
        
        Args:
            query_name: Имя карты для поиска (например, "Reno" или "Рено")
            
        Returns:
            Словарь с данными карты (dbfId, id, name) или None, если не найдено
        """
        if not query_name:
            return None
        
        query_lower = query_name.lower().strip()
        # Убираем лишние пробелы и нормализуем запрос
        query_words = [w for w in query_lower.split() if w]
        query_normalized = ' '.join(query_words)
        
        exact_match = None
        partial_matches = []
        word_matches = []  # Совпадения по словам
        
        # Ищем по всем картам
        for dbf_id, card in self.cards.items():
            card_name_en = card.get('name', '')
            card_name_ru = card.get('name_ru', '')
            
            # Пропускаем карты без названия
            if not card_name_en:
                continue
                
            card_name_en_lower = card_name_en.lower()
            card_name_ru_lower = card_name_ru.lower() if card_name_ru and card_name_ru != card_name_en else ''
            
            # Точное совпадение по английскому названию
            if card_name_en_lower == query_normalized:
                exact_match = {
                    'dbfId': dbf_id,
                    'id': card.get('id', ''),
                    'name': card_name_en
                }
                break
            
            # Точное совпадение по русскому названию
            if card_name_ru_lower and card_name_ru_lower == query_normalized:
                exact_match = {
                    'dbfId': dbf_id,
                    'id': card.get('id', ''),
                    'name': card_name_en
                }
                break
            
            # Поиск по словам (все слова запроса должны быть в названии)
            if len(query_words) > 1:
                card_words_en = set(card_name_en_lower.split())
                card_words_ru = set(card_name_ru_lower.split()) if card_name_ru_lower else set()
                
                if all(word in card_words_en or word in card_words_ru for word in query_words):
                    word_matches.append({
                        'dbfId': dbf_id,
                        'id': card.get('id', ''),
                        'name': card_name_en,
                        'match_type': 'ru' if any(word in card_words_ru for word in query_words) else 'en',
                        'score': sum(1 for word in query_words if word in card_words_en or word in card_words_ru)
                    })
            
            # Частичное совпадение по английскому названию (подстрока)
            if query_normalized in card_name_en_lower:
                partial_matches.append({
                    'dbfId': dbf_id,
                    'id': card.get('id', ''),
                    'name': card_name_en,
                    'match_type': 'en',
                    'position': card_name_en_lower.find(query_normalized)
                })
            
            # Частичное совпадение по русскому названию (подстрока)
            if card_name_ru_lower and query_normalized in card_name_ru_lower:
                partial_matches.append({
                    'dbfId': dbf_id,
                    'id': card.get('id', ''),
                    'name': card_name_en,
                    'match_type': 'ru',
                    'position': card_name_ru_lower.find(query_normalized)
                })
        
        # Возвращаем точное совпадение, если есть
        if exact_match:
            return exact_match
        
        # Возвращаем совпадение по словам (более релевантное)
        if word_matches:
            word_matches.sort(key=lambda x: (-x['score'], x.get('match_type', 'en') != 'ru', len(x['name'])))
            return {k: v for k, v in word_matches[0].items() if k not in ('match_type', 'score')}
        
        # Возвращаем частичное совпадение
        if partial_matches:
            # Сортируем: сначала русские совпадения, потом по позиции (начало названия лучше), потом по длине
            partial_matches.sort(key=lambda x: (
                x.get('match_type', 'en') != 'ru',
                x.get('position', 999),
                len(x['name'])
            ))
            return {k: v for k, v in partial_matches[0].items() if k not in ('match_type', 'position')}
        
        return None

