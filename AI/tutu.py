# tutu.py

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import httpx
from aiogram import types

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

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
        (r'(\d{1,2})\.(\d{1,2})\.(\d{4})', lambda m: f"{m[3]}-{m[2]:0>2}-{m[1]:0>2}"),
        (r'(\d{1,2})\.(\d{1,2})\.(\d{2})', lambda m: f"20{m[3]}-{m[2]:0>2}-{m[1]:0>2}"),
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
        
        logging.info(f"Запрос: {CITY_ID_TO_NAME.get(origin_id, origin_id)} → {CITY_ID_TO_NAME.get(destination_id, destination_id)}, {departure_date}")
        logging.debug(f"Payload: {payload}")
        
        start_time = datetime.now()
        
        async with httpx.AsyncClient(timeout=10.0, http2=True) as client:
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
                logging.error("Таймаут запроса (10s)")
                return []
            except httpx.RequestError as e:
                logging.error(f"Ошибка сети: {e}")
                return []
                
    except Exception as e:
        logging.error(f"Ошибка в fetch_offers: {e}")
        return []


def parse_offer(offer: Dict) -> Optional[Dict]:
    """
    Парсит оффер Tutu (API 2026).
    v7.0: Чистый парсинг данных. Ссылка формируется в search_tickets.
    """
    try:
        if not isinstance(offer, dict):
            return None

        result = {
            "price": 0, "currency": "RUB", "airline": "Неизвестно",
            "departure": "", "arrival": "", "duration": "",
            "stops": 0, "baggage": False, "deeplink": ""
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

        legs = []
        for rid in route_ids_raw:
            if not isinstance(rid, str):
                continue

            if rid in routes_dict:
                route_obj = routes_dict[rid]
                seg_ids = route_obj.get("segmentIds", [])
                for seg_id in seg_ids:
                    seg = segments_dict.get(seg_id)
                    if seg:
                        legs.append(seg)
            elif rid in segments_dict:
                legs.append(segments_dict[rid])
            elif rid in voyages_dict:
                legs.append(voyages_dict[rid])
            else:
                parts = rid.split('/')
                for part in parts:
                    if part in segments_dict:
                        legs.append(segments_dict[part])
                    elif part in voyages_dict:
                        legs.append(voyages_dict[part])

        if not legs:
            return None

        first_leg = legs[0]
        last_leg = legs[-1]

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

        total_duration = sum(
            leg.get("durationMinutes", 0) or leg.get("duration", 0) for leg in legs
        )
        hours = total_duration // 60
        minutes = total_duration % 60
        result["duration"] = f"{hours}ч {minutes}м" if minutes else f"{hours}ч"

        result["stops"] = len(legs) - 1

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

        return result

    except Exception:
        return None


async def search_tickets(
    origin_name: str,
    destination_name: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
) -> List[Dict]:
    """
    Полный цикл поиска. Генерирует ссылку с IATA-кодами (MOW, IST).
    """
    origin_id = await resolve_city_id(origin_name)
    destination_id = await resolve_city_id(destination_name)
    
    if not origin_id or not destination_id:
        return []
    
    offers = await fetch_offers(origin_id, destination_id, departure_date, return_date, passengers)
    
    if not offers:
        return []

    # === ГЕНЕРАЦИЯ ПРАВИЛЬНОЙ ССЫЛКИ (IATA) ===
    try:
        # 1. Справочник популярных IATA кодов (Фолбек)
        # ID взяты из вашего CITY_MAPPING
        STATIC_IATA = {
            491: "MOW", # Москва
            419: "IST", # Стамбул
            494: "LED", # Питер
            461: "AER", # Сочи
            497: "SVX", # Екатеринбург
            496: "KZN", # Казань
            498: "OVB", # Новосибирск
            499: "VVO", # Владивосток
            500: "KGD", # Калининград
            501: "KRR", # Краснодар
            502: "KUF", # Самара
            503: "UFA", # Уфа
            504: "ROV", # Ростов
            505: "PEE", # Пермь
            506: "KJA", # Красноярск
            507: "VOZ", # Воронеж
            508: "VOG", # Волгоград
        }

        # 2. Пытаемся достать коды из ответа API
        from_code = STATIC_IATA.get(origin_id)
        to_code = STATIC_IATA.get(destination_id)

        # Если в статике нет, ищем в dictionary
        if not from_code or not to_code:
            try:
                dct = offers[0].get("_dictionary", {})
                common = dct.get("common", {})
                
                def find_iata(city_id):
                    cid = str(city_id)
                    # Ищем в городах
                    if "cities" in common and cid in common["cities"]:
                        return common["cities"][cid].get("code")
                    # Ищем в аэропортах (points)
                    if "points" in common and cid in common["points"]:
                        return common["points"][cid].get("code")
                    return None

                if not from_code:
                    from_code = find_iata(origin_id)
                if not to_code:
                    to_code = find_iata(destination_id)
            except Exception:
                pass

        # Если совсем ничего не нашли, используем ID (хоть шанс успеха мал)
        if not from_code:
            from_code = str(origin_id)
        if not to_code:
            to_code = str(destination_id)

        # 3. Формат даты: DDMMYYYY
        dep_dt = datetime.strptime(departure_date, "%Y-%m-%d")
        date_str = dep_dt.strftime("%d%m%Y")
        
        # 4. Собираем ссылку (кодируем скобки для Telegram)
        # https://avia.tutu.ru/offers/?passengers=1&route[0]=MOW-IST-05022026&changes=all
        search_link = (
            f"https://avia.tutu.ru/offers/?"
            f"passengers={passengers}"
            f"&route%5B0%5D={from_code}-{to_code}-{date_str}"
            f"&changes=all"
        )
        
        if return_date:
            ret_dt = datetime.strptime(return_date, "%Y-%m-%d")
            ret_str = ret_dt.strftime("%d%m%Y")
            search_link += f"&route%5B1%5D={to_code}-{from_code}-{ret_str}"
            
        logging.info(f"Сгенерирована ссылка: {search_link}")

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
    
    for origin in origins:
        for destination in destinations:
            tickets = await search_tickets(
                origin["name"],
                destination["name"],
                departure_date,
                return_date,
                passengers
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


def format_tickets_message(tickets: List[Dict], params: Dict) -> str:
    """Форматирует список билетов в HTML для Telegram."""
    if not tickets:
        return "😢 Билеты не найдены"
    
    # Заголовок
    origins = params.get("origins", [])
    destinations = params.get("destinations", [])
    
    origin_str = origins[0]["name"].title() if origins else "—"
    dest_str = ", ".join([d["name"].title() for d in destinations]) if destinations else "—"
    
    departure = params.get("departure_date", "")
    return_date = params.get("return_date", "")
    
    header = f"✈️ <b>Авиабилеты: {origin_str} → {dest_str}</b>\n"
    
    if return_date:
        header += f"📅 {departure} - {return_date} (туда-обратно)\n"
    else:
        header += f"📅 {departure}\n"
    
    header += f"👥 {params.get('passengers', 1)} пасс.\n\n"
    
    lines = [header]
    
    for i, ticket in enumerate(tickets, 1):
        link = ticket.get("deeplink", "#")
        airline = ticket.get("airline", "Неизвестно")
        
        lines.append(f"<b>{i}. <a href='{link}'>{airline}</a></b>")
        
        if ticket.get("scenario"):
            lines.append(f"🎯 <i>{ticket['scenario']}</i>")
        
        # Время и длительность
        departure_time = ticket.get("departure", "")
        arrival_time = ticket.get("arrival", "")
        duration = ticket.get("duration", "")
        
        if departure_time and arrival_time:
            # Извлекаем только время (HH:MM)
            dep_time_short = departure_time.split("T")[1][:5] if "T" in departure_time else ""
            arr_time_short = arrival_time.split("T")[1][:5] if "T" in arrival_time else ""
            
            if dep_time_short and arr_time_short:
                lines.append(f"🕒 {dep_time_short} → {arr_time_short} ({duration})")
        
        # Пересадки
        stops = ticket.get("stops", 0)
        if stops == 0:
            lines.append("✈️ Прямой рейс")
        else:
            lines.append(f"🔄 {stops} пересадка" if stops == 1 else f"🔄 {stops} пересадки")
        
        # Багаж
        if ticket.get("baggage"):
            lines.append("🧳 Багаж включен")
        else:
            lines.append("🧳 Без багажа")
        
        # AI комментарий
        if ticket.get("ai_reason"):
            lines.append(f"🤖 <i>{ticket['ai_reason']}</i>")
        
        # Цена
        price = ticket.get("price", 0)
        currency = ticket.get("currency", "RUB")
        symbol = "₽" if currency == "RUB" else currency
        
        lines.append(f"💰 <b>{price:,} {symbol}</b>\n")
    
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
                    origins, destinations, date, None, params["passengers"]
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
            
            all_tickets = await multi_destination_search(
                origins, destinations, departure, return_date, params["passengers"]
            )
        
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
