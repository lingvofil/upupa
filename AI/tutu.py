# tutu.py

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import httpx
from aiogram import types

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID


def get_date_range_neighbors(date_str: Optional[str]) -> List[Optional[str]]:
    if not date_str:
        return [None]
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return [
        (dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        date_str,
        (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    ]

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

TUTU_API_URL = "https://offers-api.tutu.ru/avia/offers"
TUTU_AUTOCOMPLETE_URL = "https://autocomplete-api.tutu.ru/v1/suggest"
TUTU_REFERER = "https://avia.tutu.ru/"

# Маппинг месяцев
MONTH_MAPPING = {
    "январь": 1, "января": 1,
    "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7,
    "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11,
    "декабрь": 12, "декабря": 12,
}

# Маппинг городов на CityId (только ПРОВЕРЕННЫЕ значения)
# Для остальных городов используется динамический поиск через autocomplete API
CITY_MAPPING = {
    # ✅ ПРОВЕРЕНО из браузера
    "москва": 491,
    "мск": 491,
    "стамбул": 419,

    # ✅ Дополнительные направления (из запроса)
    "фукуок": 2167,
    "нячанг": 2161,
    "мале": 318,
    "мальдивы": 318,
    "коломбо": 279,
    "шри-ланка": 279,
    "шри ланка": 279,
    "гоа": 199,
    "бали": 2783,
    
    # ⚠️ Предположительно (требуют проверки)
    "питер": 494,
    "санкт-петербург": 494,
    "спб": 494,
    "екатеринбург": 497,
    "казань": 496,
    "сочи": 461,
    "новосибирск": 498,
    "владивосток": 499,
    "калининград": 500,
    "краснодар": 501,
    "самара": 502,
    "уфа": 503,
    "ростов": 504,
    "ростов-на-дону": 504,
    "пермь": 505,
    "красноярск": 506,
    "воронеж": 507,
    "волгоград": 508,
    
    # Для остальных городов будет использоваться autocomplete API
}

# Обратный маппинг для форматирования
CITY_ID_TO_NAME = {v: k for k, v in CITY_MAPPING.items()}


async def get_city_id_from_api(city_name: str) -> Optional[int]:
    """
    Получает CityId через Tutu autocomplete API.
    
    Используется как fallback, если города нет в CITY_MAPPING.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        
        params = {
            "query": city_name,
            "lang": "ru"
        }
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                TUTU_AUTOCOMPLETE_URL,
                headers=headers,
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                
                if items and len(items) > 0:
                    city_id = items[0].get("id")
                    city_real_name = items[0].get("name", city_name)
                    
                    logging.info(f"Найден город '{city_real_name}' с ID {city_id}")
                    return city_id
        
        logging.warning(f"Город '{city_name}' не найден через autocomplete API")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка получения CityId для '{city_name}': {e}")
        return None


async def resolve_city_id(city_name: str) -> Optional[int]:
    """
    Резолвит название города в CityId.
    
    1. Сначала проверяет CITY_MAPPING
    2. Если не найдено - запрашивает через autocomplete API
    """
    city_lower = city_name.lower().strip()
    
    # Проверяем статический маппинг
    if city_lower in CITY_MAPPING:
        return CITY_MAPPING[city_lower]
    
    # Запрашиваем через API
    logging.info(f"Город '{city_name}' не найден в маппинге, запрашиваю через API...")
    return await get_city_id_from_api(city_name)


def parse_date(date_str: str) -> Optional[str]:
    """
    Парсит дату из строки.
    Поддерживаемые форматы:
    - DD.MM
    - DD.MM.YY
    - DD.MM.YYYY
    
    Returns: дата в формате YYYY-MM-DD или None
    """
    patterns = [
        (r'(\d{1,2})\.(\d{1,2})\.(\d{4})', lambda m: f"{m[2]}-{m[1]:0>2}-{m[0]:0>2}"),
        (r'(\d{1,2})\.(\d{1,2})\.(\d{2})', lambda m: f"20{m[2]}-{m[1]:0>2}-{m[0]:0>2}"),
        (r'(\d{1,2})\.(\d{1,2})', lambda m: None),
    ]
    
    for pattern, formatter in patterns:
        match = re.search(pattern, date_str)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                day, month = groups
                current_year = datetime.now().year
                try:
                    date = datetime(current_year, int(month), int(day))
                    if date < datetime.now():
                        date = date.replace(year=current_year + 1)
                    return date.strftime("%Y-%m-%d")
                except ValueError:
                    return None
            else:
                return formatter(groups)
    
    return None


def parse_date_range(text: str) -> Optional[Tuple[str, str]]:
    """
    Парсит диапазон дат из строки.
    Поддерживаемые форматы:
    - 18.05-25.05
    - 18.05.26-25.05.26
    
    Returns: (start_date, end_date) в формате YYYY-MM-DD или None
    """
    pattern = r'(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s*-\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)'
    match = re.search(pattern, text)
    
    if match:
        start_str, end_str = match.groups()
        start_date = parse_date(start_str)
        end_date = parse_date(end_str)
        
        if start_date and end_date:
            return (start_date, end_date)
    
    return None


def parse_search_command(text: str) -> Dict:
    """
    Парсит команду поиска билетов.
    
    Паттерны:
    - "билеты Сочи" → Москва-Сочи, завтра
    - "билеты Казань Питер" → Казань-Питер
    - "билеты Дубай 18.05" → Москва-Дубай, 18.05
    - "билеты Пхукет 10.12-25.12" → Москва-Пхукет, туда-обратно
    - "билеты Стамбул май" → Москва-Стамбул, весь май
    - "билеты Сочи Пхукет Дубай июнь" → Множественные направления
    
    Returns:
    {
        "origins": [{"name": "москва"}],  # CityId резолвится позже
        "destinations": [{"name": "сочи"}, ...],
        "departure_date": "2026-05-18" или None,
        "return_date": "2026-05-25" или None,
        "month": 5 или None,
        "passengers": 1
    }
    """
    text_lower = text.lower().strip()
    
    if text_lower.startswith("билеты"):
        text_lower = text_lower[6:].strip()
    
    params = {
        "origins": [],
        "destinations": [],
        "departure_date": None,
        "return_date": None,
        "month": None,
        "passengers": 1,
    }
    
    # 1. Проверяем наличие точных дат
    date_range = parse_date_range(text_lower)
    if date_range:
        params["departure_date"] = date_range[0]
        params["return_date"] = date_range[1]
        logging.info(f"Найдены даты: {date_range[0]} - {date_range[1]}")
    else:
        for word in text_lower.split():
            if '.' in word:
                date = parse_date(word)
                if date:
                    params["departure_date"] = date
                    break
    
    # 2. Поиск месяца
    if not params["departure_date"]:
        for word in text_lower.split():
            if word in MONTH_MAPPING:
                params["month"] = MONTH_MAPPING[word]
                break
    
    # 3. Поиск ВСЕХ городов (сохраняем только имена)
    found_cities = []
    for city_name in CITY_MAPPING.keys():
        if city_name in text_lower:
            if not any(c["name"] == city_name for c in found_cities):
                found_cities.append({"name": city_name})
    
    # 4. Определяем origins и destinations
    if not found_cities:
        params["origins"] = [{"name": "москва"}]
    elif len(found_cities) == 1:
        params["origins"] = [{"name": "москва"}]
        params["destinations"] = found_cities
    elif len(found_cities) == 2:
        params["origins"] = [found_cities[0]]
        params["destinations"] = [found_cities[1]]
    else:
        params["origins"] = [{"name": "москва"}]
        params["destinations"] = found_cities
    
    # 5. Если нет даты и месяца → завтра
    if not params["departure_date"] and not params["month"]:
        tomorrow = datetime.now() + timedelta(days=1)
        params["departure_date"] = tomorrow.strftime("%Y-%m-%d")
    
    return params


def generate_month_dates(month: int) -> List[str]:
    """Генерирует список дат для заданного месяца."""
    today = datetime.now()
    year = today.year if month >= today.month else today.year + 1
    
    dates = []
    day = 1
    while True:
        try:
            date = datetime(year, month, day)
            if date >= today:
                dates.append(date.strftime("%Y-%m-%d"))
            day += 1
        except ValueError:
            break
    
    return dates


async def fetch_offers(
    origin_id: int,
    destination_id: int,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    client: Optional[httpx.AsyncClient] = None
) -> List[Dict]:
    """
    Получает предложения с Tutu API.
    
    КРИТИЧНО: единственный источник данных - POST /avia/offers
    
    Returns: список офферов или []
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://avia.tutu.ru",
            "Referer": TUTU_REFERER,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        
        # Формируем маршруты
        routes = [
            {
                "departureCityId": origin_id,
                "arrivalCityId": destination_id,
                "departureDate": departure_date
            }
        ]
        
        # Если туда-обратно
        if return_date:
            routes.append({
                "departureCityId": destination_id,
                "arrivalCityId": origin_id,
                "departureDate": return_date
            })
        
        # Генерируем обязательные ID (как в браузере)
        session_id = str(uuid.uuid4())
        search_id = str(uuid.uuid4())
        page_id = ''.join(chr(ord('a') + i % 26) for i in range(11))  # Генерируем случайный pageId
        
        payload = {
            "passengers": {
                "full": passengers,
                "child": 0,
                "infant": 0
            },
            "serviceClass": "Y",
            "routes": routes,
            "pageId": page_id,
            "searchId": search_id,
            "sessionId": session_id,
            "source": "offers",
            "userData": {
                "screenSize": "md"
            }
        }
        
        logging.info(f"Запрос: {CITY_ID_TO_NAME.get(origin_id, origin_id)} → {CITY_ID_TO_NAME.get(destination_id, destination_id)}, {departure_date}")
        logging.debug(f"Payload: {payload}")
        
        start_time = datetime.now()
        
        owned_client = None
        if client is None:
            owned_client = httpx.AsyncClient(timeout=30.0, http2=True)
            client = owned_client
        try:
            response = await client.post(
                TUTU_API_URL,
                headers=headers,
                json=payload
            )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            logging.info(f"HTTP {response.status_code}, время: {elapsed:.2f}s")
            
            if response.status_code != 200:
                logging.error(f"Ошибка API: {response.status_code}")
                return []
            
            data = response.json()
            
            logging.debug(f"Тип ответа: {type(data)}")
            
            # API возвращает список с одним элементом-словарем
            if isinstance(data, list) and len(data) > 0:
                logging.debug(f"Ответ - список из {len(data)} элементов, берем первый")
                data = data[0]
            
            if not isinstance(data, dict):
                logging.error(f"Неожиданный тип ответа: {type(data)}")
                return []
            
            logging.debug(f"Ключи верхнего уровня: {list(data.keys())}")
            
            # Офферы находятся в offers.actual
            offers_dict = data.get("offers", {})
            logging.debug(f"Тип offers: {type(offers_dict)}")
            
            if isinstance(offers_dict, dict):
                logging.debug(f"Ключи offers: {list(offers_dict.keys())}")
                
                actual = offers_dict.get("actual", {})
                logging.debug(f"Тип actual: {type(actual)}")
                
                if isinstance(actual, dict):
                    logging.info(f"Количество офферов в actual: {len(actual)}")
                    
                    if not actual:
                        # Проверяем, может быть офферы в других полях
                        future = offers_dict.get("future")
                        past = offers_dict.get("past")
                        logging.warning(f"actual пустой. future: {type(future)}, past: {type(past)}")
                        
                        # Выводим warnings если есть
                        warnings = data.get("warnings", [])
                        if warnings:
                            logging.warning(f"API warnings: {warnings}")
                        
                        return []
                    
                    offers = actual
                else:
                    logging.error(f"actual не является словарем: {type(actual)}")
                    return []
            else:
                logging.error(f"Неожиданная структура offers: {type(offers_dict)}")
                return []
            
            # offers.actual - это словарь, где ключи - ID офферов
            # Преобразуем в список
            offers_list = []
            if isinstance(offers, dict):
                dictionary = data.get("dictionary", {})
                for offer_id, offer_data in offers.items():
                    # Добавляем ID к данным оффера
                    offer_data["id"] = offer_id
                    # Добавляем ссылку на dictionary для парсинга
                    offer_data["_dictionary"] = dictionary
                    offers_list.append(offer_data)
            
            logging.info(f"Получено {len(offers_list)} офферов")
            return offers_list
            
        except httpx.TimeoutException:
            logging.error("Таймаут запроса (30s)")
            return []
        except httpx.RequestError as e:
            logging.error(f"Ошибка сети: {e}")
            return []
        finally:
            if owned_client:
                await owned_client.aclose()
                
    except Exception as e:
        logging.error(f"Ошибка в fetch_offers: {e}")
        return []


def parse_offer(offer: Dict) -> Optional[Dict]:
    """
    Парсит оффер Tutu (API 2026).
    v7.0: Чистый парсинг данных. Ссылка формируется в search_tickets.
    """
    try:
        def format_duration(minutes: int) -> str:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}ч {mins}м" if mins else f"{hours}ч"

        if not isinstance(offer, dict):
            return None

        result = {
            "price": 0, "currency": "RUB", "airline": "Неизвестно",
            "departure": "", "arrival": "", "duration": "",
            "stops": 0, "baggage": False, "deeplink": "", "trips": []
        }

        # 1. Словари
        dictionary = offer.get("_dictionary", {})
        common_dict = dictionary.get("common", {})
        avia_dict = dictionary.get("avia", {})

        segments_dict = common_dict.get("segments", {})
        routes_dict = common_dict.get("routes", {})
        voyages_dict = avia_dict.get("voyages", {})

        # 2. Цена
        offer_variants = offer.get("offerVariants")
        current_variant = {}
        if offer_variants:
            if isinstance(offer_variants, list) and len(offer_variants) > 0:
                current_variant = offer_variants[0]
            elif isinstance(offer_variants, dict):
                current_variant = next(iter(offer_variants.values()))

        price_obj = current_variant.get("price") or offer.get("price", {})
        if isinstance(price_obj, (int, float)):
            result["price"] = int(price_obj)
        elif isinstance(price_obj, dict):
            val = price_obj.get("value")
            if isinstance(val, dict):
                amt = val.get("amount", 0)
                if val.get("fraction") == 100:
                    amt //= 100
                result["price"] = int(amt)
                result["currency"] = val.get("currencyCode", "RUB")
            elif "amount" in price_obj:
                result["price"] = int(price_obj["amount"])

        if result["price"] == 0:
            return None

        # 3. Маршруты
        route_ids_raw = offer.get("routeIds") or current_variant.get("routeIds")
        if not route_ids_raw:
            route_ids_raw = offer.get("segmentIds") or current_variant.get("segmentIds")

        if not route_ids_raw:
            return None

        def collect_legs_for_route(route_id: str) -> List[Dict]:
            collected = []
            if route_id in routes_dict:
                route_obj = routes_dict[route_id]
                seg_ids = route_obj.get("segmentIds", [])
                for seg_id in seg_ids:
                    seg = segments_dict.get(seg_id)
                    if seg:
                        collected.append(seg)
            elif route_id in segments_dict:
                collected.append(segments_dict[route_id])
            elif route_id in voyages_dict:
                collected.append(voyages_dict[route_id])
            else:
                parts = route_id.split('/')
                for part in parts:
                    if part in segments_dict:
                        collected.append(segments_dict[part])
                    elif part in voyages_dict:
                        collected.append(voyages_dict[part])
            return collected

        trips = []
        for rid in route_ids_raw:
            if not isinstance(rid, str):
                continue
            legs = collect_legs_for_route(rid)
            if legs:
                trips.append(legs)

        if not trips:
            return None

        first_leg = trips[0][0]
        last_leg = trips[-1][-1]

        # 4. Детали
        result["departure"] = (
            first_leg.get("departureDateTime")
            or first_leg.get("departureTime")
            or first_leg.get("departureDate")
            or first_leg.get("datetimeBeg", "")
        )

        result["arrival"] = (
            last_leg.get("arrivalDateTime")
            or last_leg.get("arrivalTime")
            or last_leg.get("arrivalDate")
            or last_leg.get("datetimeEnd", "")
        )

        total_duration = 0
        total_stops = 0
        for trip_legs in trips:
            trip_duration = sum(
                leg.get("durationMinutes", 0) or leg.get("duration", 0) for leg in trip_legs
            )
            total_duration += trip_duration
            total_stops += max(len(trip_legs) - 1, 0)
            trip_first_leg = trip_legs[0]
            trip_last_leg = trip_legs[-1]
            trips_info = {
                "departure": (
                    trip_first_leg.get("departureDateTime")
                    or trip_first_leg.get("departureTime")
                    or trip_first_leg.get("departureDate")
                    or trip_first_leg.get("datetimeBeg", "")
                ),
                "arrival": (
                    trip_last_leg.get("arrivalDateTime")
                    or trip_last_leg.get("arrivalTime")
                    or trip_last_leg.get("arrivalDate")
                    or trip_last_leg.get("datetimeEnd", "")
                ),
                "duration": format_duration(trip_duration),
                "stops": max(len(trip_legs) - 1, 0),
            }
            result["trips"].append(trips_info)

        result["duration"] = format_duration(total_duration)
        result["stops"] = total_stops

        carrier_name = "Неизвестно"
        carrier_id = first_leg.get("carrier")
        if not carrier_id:
            carriers_list = first_leg.get("carriers", [])
            if carriers_list:
                carrier_id = carriers_list[0].get("id")

        if carrier_id:
            carriers_dict = common_dict.get("carriers", {})
            c_obj = carriers_dict.get(str(carrier_id)) or carriers_dict.get(carrier_id)
            if c_obj:
                carrier_name = c_obj.get("name", "Неизвестно")

        result["airline"] = carrier_name

        fare_id = current_variant.get("fareApplicationId") or offer.get("fareApplicationId")
        if fare_id:
            conditions = avia_dict.get("conditions", {})
            fare = conditions.get(str(fare_id))
            if fare:
                baggage = fare.get("baggage", {})
                if isinstance(baggage, dict):
                    result["baggage"] = baggage.get("included", False) or (
                        baggage.get("weight", 0) > 0
                    )
                elif isinstance(baggage, bool):
                    result["baggage"] = baggage
        if result["trips"]:
            for trip in result["trips"]:
                trip["baggage"] = result["baggage"]

        return result

    except Exception:
        return None


async def search_tickets(
    origin_name: str,
    destination_name: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    client: Optional[httpx.AsyncClient] = None
) -> List[Dict]:
    """
    Полный цикл поиска. 
    Генерирует ссылку в формате Tutu: ID_ОТКУДА - ДАТА - ID_КУДА.
    """
    origin_id = await resolve_city_id(origin_name)
    destination_id = await resolve_city_id(destination_name)
    
    if not origin_id or not destination_id:
        return []
    
    # 1. Запрос к API (тут порядок не важен, главное правильные ID)
    offers = await fetch_offers(
        origin_id,
        destination_id,
        departure_date,
        return_date,
        passengers,
        client=client
    )
    
    if not offers:
        return []

    # === ГЕНЕРАЦИЯ ССЫЛКИ (TUTU FORMAT) ===
    try:
        # Формат даты: DDMMYYYY (слитно)
        dep_dt = datetime.strptime(departure_date, "%Y-%m-%d")
        date_str = dep_dt.strftime("%d%m%Y")
        
        # Ссылка: route[0] = ОТКУДА - ДАТА - КУДА
        # Пример: 491-05022026-419
        search_link = (
            f"https://avia.tutu.ru/offers/?"
            f"passengers={passengers}&class=Y"
            f"&route[0]={origin_id}-{date_str}-{destination_id}"
            f"&changes=all"
        )
        
        # Обратный билет: route[1] = КУДА - ДАТА - ОТКУДА
        if return_date:
            ret_dt = datetime.strptime(return_date, "%Y-%m-%d")
            ret_str = ret_dt.strftime("%d%m%Y")
            search_link += f"&route[1]={destination_id}-{ret_str}-{origin_id}"

    except Exception as e:
        logging.error(f"Ошибка ссылки: {e}")
        search_link = "https://avia.tutu.ru/"
    # ==========================================

    logging.info(f"Начинаю парсинг {len(offers)} офферов...")

    tickets = []

    for offer in offers:
        ticket = parse_offer(offer)
        if ticket and ticket["price"] > 0:
            ticket["deeplink"] = search_link
            tickets.append(ticket)

    tickets.sort(key=lambda x: x["price"])
    return tickets[:7]


async def multi_destination_search(
    origins: List[Dict],
    destinations: List[Dict],
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
) -> List[Dict]:
    """
    Поиск билетов по множественным направлениям.
    
    Returns: объединенный список всех найденных билетов
    """
    all_tickets = []
    
    async with httpx.AsyncClient(timeout=30.0, http2=True) as client:
        for origin in origins:
            for destination in destinations:
                tickets = await search_tickets(
                    origin["name"],
                    destination["name"],
                    departure_date,
                    return_date,
                    passengers,
                    client=client
                )
                
                # Добавляем метаданные
                for ticket in tickets:
                    ticket["origin_name"] = origin["name"]
                    ticket["destination_name"] = destination["name"]
                
                all_tickets.extend(tickets)
                
                # Задержка между запросами
                await asyncio.sleep(2)
    
    return all_tickets


async def analyze_tickets_with_ai(tickets: List[Dict], params: Dict) -> List[Dict]:
    """
    AI-анализ билетов с рекомендациями.
    
    Использует Groq для генерации комментариев к каждому билету.
    """
    if not tickets:
        return []

    # Сортируем и берем топ-10 основных и топ-10 альтернатив для анализа
    mains = sorted([t for t in tickets if not t.get("is_alternative")], key=lambda x: x["price"])[:10]
    alts = sorted([t for t in tickets if t.get("is_alternative")], key=lambda x: x["price"])[:10]
    subset = mains + alts

    # Упрощаем данные для AI, чтобы JSON был компактным и валидным
    simplify = []
    for t in subset:
        simplify.append({
            "id": t["id"],
            "price": t["price"],
            "is_alt": t.get("is_alternative"),
            "date": f"{t.get('search_departure')} - {t.get('search_return')}",
            "airline": t.get("airline")
        })

    prompt = f"""
    Ты эксперт по авиабилетам. Выбери из списка 5-7 лучших предложений.
    ОБЯЗАТЕЛЬНО:
    1. Включи 2-3 лучших билета на ОСНОВНЫЕ даты (is_alt: false).
    2. Включи лучшие АЛЬТЕРНАТИВЫ (is_alt: true), если они дешевле или удобнее.
    3. Для каждого выбранного билета напиши 'scenario' (почему это выгодно).
    
    Ответ дай ТОЛЬКО в формате JSON массива:
    [ {{"id": \"...\", \"scenario\": \"...\"}}, ... ]
    
    Данные: {json.dumps(simplify, ensure_ascii=False)}
    """

    try:
        response = await groq_ai.generate_text(prompt)
        # Улучшенный парсинг: ищем первый '[' и последний ']'
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            suggestions = json.loads(match.group(0))
            # Сопоставляем id из AI с полными данными билетов
            result = []
            for sug in suggestions:
                orig = next((t for t in subset if t["id"] == sug["id"]), None)
                if orig:
                    orig["scenario"] = sug.get("scenario", "")
                    result.append(orig)
            return result
    except Exception as e:
        logging.error(f"AI parsing error: {e}")
    return []


def format_tickets_message(tickets: List[Dict], params: Dict) -> str:
    if not tickets:
        return "😢 Билеты не найдены"

    main_tickets = [t for t in tickets if not t.get("is_alternative")]

    # Находим самый дешевый основной билет для сравнения
    min_main_price = min([t["price"] for t in main_tickets]) if main_tickets else float("inf")

    # Берем альтернативы, которые дешевле основного хотя бы на 500р
    alt_tickets = [
        t for t in tickets
        if t.get("is_alternative") and t["price"] < (min_main_price - 500)
    ]

    # Если AI упал и мы в фолбеке, отсортируем всё по цене
    if not any(t.get("scenario") for t in tickets):
        main_tickets.sort(key=lambda x: x["price"])
        alt_tickets.sort(key=lambda x: x["price"])

    def render_t(t: Dict, idx: int) -> str:
        price = f"{t['price']:,}".replace(",", " ")
        link = t.get("deeplink", "#")
        scen = f"<i>💡 {t['scenario']}</i>\n" if t.get("scenario") else ""

        date_str = ""
        if t.get("is_alternative"):
            d = t.get("search_departure", "")[8:10] + "." + t.get("search_departure", "")[5:7]
            r = t.get("search_return", "")
            r_str = f" - {r[8:10]}.{r[5:7]}" if r else ""
            date_str = f"📅 <b>{d}{r_str}</b>\n"

        return (f"<b>{idx}. <a href='{link}'>{t.get('airline', 'Рейс')}</a></b>\n"
                f"{date_str}{scen}💰 <b>{price} ₽</b>\n")

    res = [f"✈️ <b>{params['origins'][0]['name'].title()} → {params['destinations'][0]['name'].title()}</b>\n"]

    if main_tickets:
        res.append("📍 <b>Ваши даты:</b>")
        for i, t in enumerate(main_tickets[:3], 1):
            res.append(render_t(t, i))
    else:
        res.append("❌ <b>На ваши даты прямых билетов не найдено</b>\n")

    if alt_tickets:
        res.append("\n🔥 <b>Выгодные альтернативы:</b>")
        # Группируем альтернативы по датам, чтобы не спамить одинаковыми
        seen_dates = set()
        count = 1
        for t in alt_tickets:
            d_key = f"{t.get('search_departure')}_{t.get('search_return')}"
            if d_key not in seen_dates and count <= 3:
                res.append(render_t(t, count))
                seen_dates.add(d_key)
                count += 1

    return "\n".join(res)


async def process_tickets_command(message: types.Message):
    """
    Главный обработчик команды поиска билетов.
    
    Примеры команд:
    - билеты Сочи
    - билеты Казань Питер
    - билеты Дубай 18.05
    - билеты Пхукет 10.12-25.12
    - билеты Стамбул май
    - билеты Сочи Пхукет Дубай июнь
    """
    # Проверка прав (опционально)
    if ADMIN_ID and message.from_user.id != int(ADMIN_ID):
        await message.reply("🚫 Доступ к поиску билетов только для администратора.")
        return
    
    try:
        params = parse_search_command(message.text)
        
        origins = params.get("origins", [])
        destinations = params.get("destinations", [])
        
        if not origins or not destinations:
            await message.reply(
                "❌ Не понял направление. Укажите города.\n\n"
                "<b>Примеры:</b>\n"
                "• <i>билеты Сочи</i>\n"
                "• <i>билеты Казань Питер</i>\n"
                "• <i>билеты Дубай 18.05</i>\n"
                "• <i>билеты Пхукет 10.12-25.12</i>\n"
                "• <i>билеты Стамбул май</i>",
                parse_mode="HTML"
            )
            return
        
        # Формируем статус-сообщение
        origin_str = ", ".join([o["name"].title() for o in origins])
        dest_str = ", ".join([d["name"].title() for d in destinations])
        
        departure = params.get("departure_date", "")
        return_date = params.get("return_date", "")
        month = params.get("month")
        
        if month:
            # Режим поиска по месяцу
            month_names = list(MONTH_MAPPING.keys())
            month_name = month_names[month * 2 - 2].title()
            
            status_msg = await message.reply(
                f"🔍 <b>Запускаю поиск билетов</b>\n\n"
                f"📍 Маршрут: {origin_str} → {dest_str}\n"
                f"📅 Месяц: {month_name}\n"
                f"👥 Пассажиров: {params['passengers']}\n\n"
                f"⏳ Сканирую весь месяц...\n"
                f"Это может занять 5-10 минут.",
                parse_mode="HTML"
            )
            
            # Генерируем даты месяца (ограничиваем 10 датами)
            dates = generate_month_dates(month)[:10]
            
            all_tickets = []
            for date in dates:
                tickets = await multi_destination_search(
                    origins, destinations, date, None, params["passengers"]
                )
                all_tickets.extend(tickets)
                await asyncio.sleep(3)
            
        else:
            # Подготовка списка комбинаций
            dep_variants = get_date_range_neighbors(departure)
            ret_variants = get_date_range_neighbors(return_date)
            
            combinations = []
            for d in dep_variants:
                for r in ret_variants:
                    combinations.append((d, r))
            
            total_steps = len(combinations)
            all_tickets = []
            
            status_msg = await message.reply(
                f"🔍 <b>Запускаю глубокий поиск</b>\n"
                f"📍 {origin_str} → {dest_str}\n"
                f"📅 Дата: {departure} {'- ' + return_date if return_date else ''}\n\n"
                f"⌛ Подбираю варианты: [░░░░░░░░░░] 0/{total_steps}",
                parse_mode="HTML"
            )

            for i, (dep_v, ret_v) in enumerate(combinations, 1):
                # Обновляем прогресс-бар
                progress = int((i / total_steps) * 10)
                bar = "▓" * progress + "░" * (10 - progress)
                await status_msg.edit_text(
                    f"🔍 <b>Глубокий поиск</b> (±1 день)\n"
                    f"📍 {origin_str} → {dest_str}\n"
                    f"📅 Проверка: {dep_v} {f' - {ret_v}' if ret_v else ''}\n\n"
                    f"⌛ [{bar}] {i}/{total_steps}",
                    parse_mode="HTML"
                )

                is_target = (dep_v == departure and (ret_v == return_date or ret_v is None))
                
                tickets = await multi_destination_search(
                    origins, destinations, dep_v, ret_v, params["passengers"]
                )
                
                for t in tickets:
                    t["is_alternative"] = not is_target
                    t["search_departure"] = dep_v
                    t["search_return"] = ret_v
                
                all_tickets.extend(tickets)
                if i < total_steps:
                    await asyncio.sleep(0.5) # Небольшая пауза

            # После сбора всех all_tickets
            all_tickets.sort(key=lambda x: x["price"])
        
        if not all_tickets:
            await status_msg.edit_text(
                "😕 Билеты не найдены.\n"
                "Попробуйте другие даты или направление."
            )
            return
        
        await status_msg.edit_text(
            f"✅ <b>Поиск завершен!</b>\n"
            f"Найдено билетов: {len(all_tickets)}\n\n"
            f"⏳ Запускаю AI-анализ...",
            parse_mode="HTML"
        )
        
        # AI анализ
        best_tickets = await analyze_tickets_with_ai(all_tickets, params)

        # Если AI ничего не вернул (ошибка), делаем ручной фолбек
        if not best_tickets and not month:
            logging.info("Использую ручной фолбек")
            # Берем 3 лучших основных и 3 лучших альтернативных
            mains = [t for t in all_tickets if not t.get("is_alternative")][:3]
            alts = [t for t in all_tickets if t.get("is_alternative")][:10]
            best_tickets = mains + alts
        
        # Страховка: если AI почему-то не оставил билеты на основные даты,
        # добавим один самый дешевый из "основных" вручную
        has_main = any(not t.get("is_alternative") for t in best_tickets)
        if not has_main:
            main_only = [t for t in all_tickets if not t.get("is_alternative")]
            if main_only:
                cheapest_main = min(main_only, key=lambda x: x["price"])
                cheapest_main["scenario"] = "Оптимальный вариант на ваши даты"
                best_tickets.insert(0, cheapest_main)
        
        if not best_tickets:
            await status_msg.edit_text("😕 Не удалось проанализировать билеты.")
            return
        
        await status_msg.delete()
        
        # Отправляем результаты
        result_text = format_tickets_message(best_tickets, params)
        await message.reply(result_text, parse_mode="HTML", disable_web_page_preview=True)
        
        logging.info(f"Отправлено {len(best_tickets)} билетов пользователю {message.from_user.id}")
        
    except Exception as e:
        logging.error(f"Ошибка в process_tickets_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(e)}")
