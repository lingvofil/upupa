# tutu.py

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
import httpx
from aiogram import types

from core.settings import ADMIN_ID
from infrastructure.ai.clients import groq_ai
logger = logging.getLogger(__name__)

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

TUTU_API_URL = "https://offers-api.tutu.ru/avia/offers"
TUTU_AUTOCOMPLETE_URL = "https://autocomplete-api.tutu.ru/v1/suggest"
TUTU_REFERER = "https://avia.tutu.ru/"
READ_TIMEOUT = 40.0
TUTU_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=READ_TIMEOUT,
    write=10.0,
    pool=10.0,
)
MAX_DATE_VARIANTS = 3

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
    "сочи": 78,
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


def format_short_date(date_str: str) -> str:
    """
    Форматирует дату YYYY-MM-DD в DD.MM.
    Если формат не распознан, возвращает исходную строку.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return date_str


def format_full_date(date_str: str) -> str:
    """Форматирует дату YYYY-MM-DD в DD.MM.YYYY."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return date_str


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


def generate_date_variants(
    departure: str,
    return_date: Optional[str]
) -> List[Tuple[str, Optional[str]]]:
    dep = datetime.strptime(departure, "%Y-%m-%d")
    ret = datetime.strptime(return_date, "%Y-%m-%d") if return_date else None

    variants = []
    for shift in (-1, 0, 1):
        new_dep = dep + timedelta(days=shift)
        new_ret = ret + timedelta(days=shift) if ret else None
        if new_ret and new_ret <= new_dep:
            continue
        variants.append((
            new_dep.strftime("%Y-%m-%d"),
            new_ret.strftime("%Y-%m-%d") if new_ret else None
        ))

    return variants[:MAX_DATE_VARIANTS]


def build_offer_meta(
    out_date: str,
    return_date: Optional[str],
    requested_out: Optional[str],
    requested_return: Optional[str]
) -> Dict:
    """Формирует метаданные дат для оффера."""
    out_dt = datetime.strptime(out_date, "%Y-%m-%d").date()
    ret_dt = (
        datetime.strptime(return_date, "%Y-%m-%d").date()
        if return_date
        else None
    )

    requested_out = requested_out or out_date
    requested_return = requested_return if requested_return is not None else return_date

    requested_out_dt = datetime.strptime(requested_out, "%Y-%m-%d").date()
    requested_ret_dt = (
        datetime.strptime(requested_return, "%Y-%m-%d").date()
        if requested_return
        else None
    )

    date_type = (
        "exact"
        if out_date == requested_out and return_date == requested_return
        else "alternative"
    )

    date_shift = (out_dt - requested_out_dt).days
    if date_shift < -1:
        date_shift = -1
    elif date_shift > 1:
        date_shift = 1

    return {
        "out_date": out_dt,
        "return_date": ret_dt,
        "date_type": date_type,
        "date_shift": date_shift,
    }


async def fetch_offers(
    origin_id: int,
    destination_id: int,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
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
        
        logger.info(f"Запрос: {CITY_ID_TO_NAME.get(origin_id, origin_id)} → {CITY_ID_TO_NAME.get(destination_id, destination_id)}, {departure_date}")
        logger.debug(f"Payload: {payload}")
        
        start_time = datetime.now()
        
        async with httpx.AsyncClient(timeout=TUTU_TIMEOUT, http2=True) as client:
            try:
                response = await client.post(
                    TUTU_API_URL,
                    headers=headers,
                    json=payload
                )

                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info(f"HTTP {response.status_code}, время: {elapsed:.2f}s")

                if response.status_code != 200:
                    logger.error(f"Ошибка API: {response.status_code}")
                    return []

                data = response.json()
                logger.debug(f"Тип ответа: {type(data)}")

                # API возвращает список с одним элементом-словарем
                if isinstance(data, list) and len(data) > 0:
                    logger.debug(f"Ответ - список из {len(data)} элементов, берем первый")
                    data = data[0]

                if not isinstance(data, dict):
                    logger.error(f"Неожиданный тип ответа: {type(data)}")
                    return []

                logger.debug(f"Ключи верхнего уровня: {list(data.keys())}")
                
                # Офферы находятся в offers.actual
                offers_dict = data.get("offers", {})
                logger.debug(f"Тип offers: {type(offers_dict)}")
                
                if isinstance(offers_dict, dict):
                    logger.debug(f"Ключи offers: {list(offers_dict.keys())}")
                    
                    actual = offers_dict.get("actual", {})
                    logger.debug(f"Тип actual: {type(actual)}")
                    
                    if isinstance(actual, dict):
                        logger.info(f"Количество офферов в actual: {len(actual)}")
                        
                        if not actual:
                            # Проверяем, может быть офферы в других полях
                            future = offers_dict.get("future")
                            past = offers_dict.get("past")
                            logger.warning(f"actual пустой. future: {type(future)}, past: {type(past)}")
                            
                            # Выводим warnings если есть
                            warnings = data.get("warnings", [])
                            if warnings:
                                logger.warning(f"API warnings: {warnings}")
                            
                            return []
                        
                        offers = actual
                    else:
                        logger.error(f"actual не является словарем: {type(actual)}")
                        return []
                else:
                    logger.error(f"Неожиданная структура offers: {type(offers_dict)}")
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
                
                logger.info(f"Получено {len(offers_list)} офферов")
                return offers_list
                
            except httpx.ReadTimeout:
                logger.warning(
                    f"Tutu API долго отвечает (> {READ_TIMEOUT}s), запрос пропущен"
                )
                return []
            except httpx.RequestError as e:
                logger.error(f"Ошибка сети: {e}")
                return []
                
    except Exception as e:
        logger.error(f"Ошибка в fetch_offers: {e}")
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
                current_variant = min(
                    offer_variants,
                    key=lambda x: x.get("price", {}).get("value", {}).get("amount", float("inf")),
                )
            elif isinstance(offer_variants, dict):
                current_variant = min(
                    offer_variants.values(),
                    key=lambda x: x.get("price", {}).get("value", {}).get("amount", float("inf")),
                )

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
    requested_departure: Optional[str] = None,
    requested_return: Optional[str] = None
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
    offers = await fetch_offers(origin_id, destination_id, departure_date, return_date, passengers)
    
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
            ticket["meta"] = build_offer_meta(
                departure_date,
                return_date,
                requested_departure,
                requested_return
            )
            tickets.append(ticket)

    tickets.sort(key=lambda x: x["price"])
    return tickets[:7]


async def multi_destination_search(
    origins: List[Dict],
    destinations: List[Dict],
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    requested_departure: Optional[str] = None,
    requested_return: Optional[str] = None
) -> List[Dict]:
    """
    Поиск билетов по множественным направлениям.
    
    Returns: объединенный список всех найденных билетов
    """
    all_tickets = []
    
    for origin in origins:
        for destination in destinations:
            tickets = await search_tickets(
                origin["name"],
                destination["name"],
                departure_date,
                return_date,
                passengers,
                requested_departure,
                requested_return
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
    if not tickets or len(tickets) == 0:
        return []
    
    # Берем топ-20 для анализа
    candidates = tickets[:20]
    
    # Статистика
    prices = [t["price"] for t in candidates]
    
    avg_price = int(sum(prices) / len(prices))
    min_price = min(prices)
    max_price = max(prices)
    
    # Определяем направления
    origins = params.get("origins", [])
    destinations = params.get("destinations", [])
    
    origin_str = origins[0]["name"].title() if origins else "неизвестно"
    dest_str = ", ".join([d["name"].title() for d in destinations]) if destinations else "неизвестно"
    
    # Даты
    departure = params.get("departure_date", "")
    return_date = params.get("return_date", "")
    display_departure = format_full_date(departure) if departure else departure
    display_return = format_full_date(return_date) if return_date else return_date
    
    date_info = f"{departure}"
    if return_date:
        date_info += f" - {return_date} (туда-обратно)"
    
    # Формируем упрощенный список для AI
    candidates_simplified = []
    for i, ticket in enumerate(candidates):
        candidates_simplified.append({
            "index": i,
            "price": ticket["price"],
            "airline": ticket["airline"],
            "duration": ticket["duration"],
            "stops": ticket["stops"],
            "baggage": ticket["baggage"]
        })
    
    prompt = f"""
Ты - профессиональный эксперт по авиабилетам. Проведи анализ и выбери ТОП-7 билетов.

КОНТЕКСТ ПОИСКА:
• Маршрут: {origin_str} → {dest_str}
• Даты: {date_info}
• Пассажиров: {params.get('passengers', 1)}

РЫНОЧНАЯ СТАТИСТИКА:
• Минимальная цена: {min_price:,} ₽
• Максимальная цена: {max_price:,} ₽
• Средняя цена: {avg_price:,} ₽

КАНДИДАТЫ (топ-20 билетов):
{candidates_simplified}

ЗАДАЧА:
Выбери ТОП-7 билетов по разным сценариям:
1. Минимальный бюджет (но адекватные условия)
2. Лучший баланс цена/время
3. Прямой рейс (если есть)
4. С багажом
5. Премиум авиакомпания
6-7. Дополнительные интересные варианты

КРИТЕРИИ:
• Пересадки: 0 - отлично, 1 - нормально, 2+ - плохо
• Авиакомпании: Turkish, Emirates, Qatar - премиум; Аэрофлот, S7 - надежно
• Багаж: важно для многих
• Все цены в РУБЛЯХ

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON массив из 7 объектов:
[
  {{
    "index": 0,
    "ai_score": 9,
    "scenario": "Минимальный бюджет",
    "reason": "S7 Airlines, 15 200 ₽. Прямой рейс 3ч 20м. Отличный вариант для налегке."
  }},
  ...
]

Поля:
• index - номер в массиве candidates (0-19)
• ai_score - оценка 1-10
• scenario - сценарий использования (2-4 слова)
• reason - комментарий (15-40 слов), конкретные факты, эмодзи приветствуются

ВАЖНО: reason должен быть информативным с цифрами и фактами!
"""

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)
            
            # Извлекаем JSON из ответа
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                import json
                ai_results = json.loads(json_match.group(0))
                
                final_tickets = []
                for item in ai_results:
                    idx = item.get('index')
                    if idx is not None and isinstance(idx, int) and 0 <= idx < len(candidates):
                        ticket = candidates[idx].copy()
                        ticket['ai_score'] = item.get('ai_score', 0)
                        ticket['scenario'] = item.get('scenario', 'Выбор AI')
                        ticket['ai_reason'] = item.get('reason', 'Рекомендация AI')
                        final_tickets.append(ticket)
                
                final_tickets.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
                
                if final_tickets:
                    logging.info(f"AI вернул {len(final_tickets)} рекомендаций")
                    return final_tickets

    except Exception as e:
        logging.error(f"Ошибка AI анализа: {e}")
    
    # Фолбек без AI
    logging.info("Использую фолбек (без AI)")
    
    # Простая оценка по value score
    for ticket in candidates:
        # Базовая оценка
        score = 10000 - ticket["price"]
        
        # Бонусы
        if ticket["stops"] == 0:
            score += 5000
        elif ticket["stops"] == 1:
            score += 2000
        
        if ticket["baggage"]:
            score += 1000
        
        ticket['value_score'] = score
    
    candidates.sort(key=lambda x: x.get('value_score', 0), reverse=True)
    
    return candidates[:7]


def format_tickets_message(
    exact_tickets: List[Dict],
    alternative_tickets: List[Dict],
    params: Dict,
    no_exact_message: Optional[str] = None
) -> str:
    """Форматирует список билетов в HTML для Telegram."""
    if isinstance(params, dict):
        requested_departure = params.get("departure_date")
        requested_return = params.get("return_date")
    else:
        requested_departure = getattr(params, "departure_date", None)
        requested_return = getattr(params, "return_date", None)

    display_departure = (
        requested_departure.strftime("%d.%m.%Y")
        if isinstance(requested_departure, (date, datetime))
        else format_full_date(requested_departure) if requested_departure else ""
    )
    display_return = (
        requested_return.strftime("%d.%m.%Y")
        if isinstance(requested_return, (date, datetime))
        else format_full_date(requested_return) if requested_return else ""
    )

    if not exact_tickets and not alternative_tickets:
        return "😢 Билеты не найдены"
    
    # Заголовок
    origins = params.get("origins", [])
    destinations = params.get("destinations", [])
    
    origin_str = origins[0]["name"].title() if origins else "—"
    dest_str = ", ".join([d["name"].title() for d in destinations]) if destinations else "—"
    
    header = f"✈️ <b>Авиабилеты: {origin_str} → {dest_str}</b>\n"
    
    if requested_return:
        header += f"📅 {display_departure} - {display_return} (туда-обратно)\n"
    else:
        header += f"📅 {display_departure}\n"
    
    header += f"👥 {params.get('passengers', 1)} пасс.\n\n"
    
    lines = [header]

    if no_exact_message:
        lines.append(f"❗️{no_exact_message}\n")

    def format_time_block(block: Dict) -> List[str]:
        block_lines = []
        departure_time = block.get("departure", "")
        arrival_time = block.get("arrival", "")
        duration = block.get("duration", "")

        if departure_time and arrival_time:
            def format_datetime(dt_str: str) -> str:
                if "T" in dt_str:
                    date_part, time_part = dt_str.split("T", 1)
                    date_short = format_short_date(date_part)
                    time_short = time_part[:5]
                    return f"{date_short} {time_short}"
                return dt_str

            dep_short = format_datetime(departure_time)
            arr_short = format_datetime(arrival_time)
            if dep_short and arr_short:
                block_lines.append(f"🕒 {dep_short} → {arr_short} ({duration})")

        stops = block.get("stops", 0)
        if stops == 0:
            block_lines.append("✈️ Прямой рейс")
        else:
            block_lines.append(f"🔄 {stops} пересадка" if stops == 1 else f"🔄 {stops} пересадки")

        if block.get("baggage"):
            block_lines.append("🧳 Багаж включен")
        else:
            block_lines.append("🧳 Без багажа")

        return block_lines

    def describe_alt_dates(tickets: List[Dict]) -> Optional[str]:
        if not tickets:
            return None
        unique_dates = {
            (
                t.get("meta", {}).get("out_date"),
                t.get("meta", {}).get("return_date")
            )
            for t in tickets
        }
        unique_dates = {
            (d_out, d_ret)
            for d_out, d_ret in unique_dates
            if isinstance(d_out, date)
        }
        if len(unique_dates) == 1:
            out_dt, ret_dt = next(iter(unique_dates))
            if ret_dt:
                return f"📅 {out_dt.strftime('%d.%m')} – {ret_dt.strftime('%d.%m')}"
            return f"📅 {out_dt.strftime('%d.%m')}"
        return None

    def render_ticket_block(tickets: List[Dict], start_index: int = 1) -> List[str]:
        block_lines = []
        for i, ticket in enumerate(tickets, start_index):
            link = ticket.get("deeplink", "#")
            airline = ticket.get("airline", "Неизвестно")

            block_lines.append(f"<b>{i}. <a href='{link}'>{airline}</a></b>")

            if ticket.get("scenario"):
                block_lines.append(f"🎯 <i>{ticket['scenario']}</i>")

            trips = ticket.get("trips") or []
            if len(trips) >= 2:
                labels = ["➡️ Туда", "↩️ Обратно"]
                for idx, trip in enumerate(trips):
                    label = labels[idx] if idx < len(labels) else f"🧭 Сегмент {idx + 1}"
                    block_lines.append(f"<b>{label}</b>")
                    block_lines.extend(format_time_block(trip))
                    if idx < len(trips) - 1:
                        block_lines.append("")
            else:
                block_lines.extend(format_time_block(ticket))

            if ticket.get("ai_reason"):
                block_lines.append(f"🤖 <i>{ticket['ai_reason']}</i>")

            price = ticket.get("price", 0)
            currency = ticket.get("currency", "RUB")
            symbol = "₽" if currency == "RUB" else currency

            block_lines.append(f"💰 <b>{price:,} {symbol}</b>\n")
        return block_lines

    if exact_tickets:
        lines.append("🟢 <b>По выбранным датам</b>")
        lines.extend(render_ticket_block(exact_tickets))

    if alternative_tickets:
        if exact_tickets:
            lines.append("────────────────────\n")
            lines.append("🟡 <b>Альтернативные даты</b>")
        else:
            lines.append("🟡 <b>Ближайшие альтернативы</b>")

        alt_date_label = describe_alt_dates(alternative_tickets)
        if alt_date_label:
            lines.append(alt_date_label)
            lines.append("")

        lines.extend(render_ticket_block(alternative_tickets))

    return "\n".join(lines)


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
                    origins,
                    destinations,
                    date,
                    None,
                    params["passengers"],
                    date,
                    None
                )
                all_tickets.extend(tickets)
                await asyncio.sleep(3)
            
        else:
            # Режим поиска с точными датами
            date_info = departure
            if return_date:
                date_info += f" - {return_date} (туда-обратно)"
            
            status_msg = await message.reply(
                f"🔍 <b>Запускаю поиск билетов</b>\n\n"
                f"📍 Маршрут: {origin_str} → {dest_str}\n"
                f"📅 Даты: {date_info}\n"
                f"👥 Пассажиров: {params['passengers']}\n\n"
                f"⏳ Ищу лучшие предложения...",
                parse_mode="HTML"
            )
            
            base_tickets = await multi_destination_search(
                origins,
                destinations,
                departure,
                return_date,
                params["passengers"],
                departure,
                return_date
            )

            alternative_tickets = []

            date_variants = [
                variant for variant in generate_date_variants(departure, return_date)
                if variant != (departure, return_date)
            ]

            for dep_alt, ret_alt in date_variants:
                tickets = await multi_destination_search(
                    origins,
                    destinations,
                    dep_alt,
                    ret_alt,
                    params["passengers"],
                    departure,
                    return_date
                )

                alternative_tickets.extend(tickets)

                await asyncio.sleep(2)
            all_tickets = base_tickets + alternative_tickets
        
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
        
        exact_offers = [o for o in all_tickets if o.get("meta", {}).get("date_type") == "exact"]
        alt_offers = [o for o in all_tickets if o.get("meta", {}).get("date_type") == "alternative"]

        best_exact = []
        best_alt = []
        if exact_offers:
            best_exact = await analyze_tickets_with_ai(exact_offers, params)
            best_exact = best_exact[:5]

            if alt_offers:
                best_alt = await analyze_tickets_with_ai(alt_offers, params)
                best_alt = best_alt[:3]
        elif alt_offers:
            best_alt = await analyze_tickets_with_ai(alt_offers, params)
            best_alt = best_alt[:7]

        if not best_exact and not best_alt:
            await status_msg.edit_text("😕 Не удалось проанализировать билеты.")
            return
        
        await status_msg.delete()
        
        no_exact_message = None
        if not best_exact and return_date:
            no_exact_message = (
                f"На даты {format_short_date(departure)} – "
                f"{format_short_date(return_date)} билеты не найдены"
            )
        elif not best_exact:
            no_exact_message = f"На дату {format_short_date(departure)} билеты не найдены"

        # Отправляем результаты
        result_text = format_tickets_message(best_exact, best_alt, params, no_exact_message)
        await message.reply(result_text, parse_mode="HTML", disable_web_page_preview=True)

        logging.info(
            f"Отправлено {len(best_exact) + len(best_alt)} билетов пользователю {message.from_user.id}"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в process_tickets_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(e)}")
