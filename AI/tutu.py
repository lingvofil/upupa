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
from config import groq_ai

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

TUTU_API_URL = "https://offers-api.tutu.ru/avia/offers"
TUTU_AUTOCOMPLETE_URL = "https://autocomplete-api.tutu.ru/v1/suggest"
TUTU_REFERER = "https://avia.tutu.ru/"

# Маппинг месяцев
MONTH_MAPPING = {
    "январь": 1, "января": 1, "01": 1,
    "февраль": 2, "февраля": 2, "02": 2,
    "март": 3, "марта": 3, "03": 3,
    "апрель": 4, "апреля": 4, "04": 4,
    "май": 5, "мая": 5, "05": 5,
    "июнь": 6, "июня": 6, "06": 6,
    "июль": 7, "июля": 7, "07": 7,
    "август": 8, "августа": 8, "08": 8,
    "сентябрь": 9, "сентября": 9, "09": 9,
    "октябрь": 10, "октября": 10, "10": 10,
    "ноябрь": 11, "ноября": 11, "11": 11,
    "декабрь": 12, "декабря": 12, "12": 12,
}

# Расширенный маппинг городов
CITY_MAPPING = {
    # РФ
    "москва": 491, "мск": 491,
    "питер": 494, "санкт-петербург": 494, "спб": 494,
    "сочи": 461, "адлер": 461,
    "казань": 496,
    "екатеринбург": 497,
    "новосибирск": 498,
    "владивосток": 499,
    "калининград": 500,

    # Турция / ОАЭ
    "стамбул": 419,
    "анталья": 396, "анталия": 396,
    "дубай": 411,

    # Азия (Новые)
    "фукуок": 2167,
    "нячанг": 2161, "камрань": 2161,
    "мале": 318, "мальдивы": 318,
    "коломбо": 279, "шри-ланка": 279, "шри ланка": 279, "цейлон": 279,
    "гоа": 199, "даболим": 199,
    "бали": 2783, "денпасар": 2783,
    "пхукет": 556,
    "бангкок": 346,
}

# IATA коды для ссылок (надежный поиск)
STATIC_IATA = {
    491: "MOW", 494: "LED", 461: "AER", 496: "KZN", 497: "SVX", 498: "OVB", 500: "KGD",
    419: "IST", 396: "AYT", 411: "DXB",
    2167: "PQC",  # Фукуок
    2161: "CXR",  # Нячанг (Камрань)
    318: "MLE",  # Мале
    279: "CMB",  # Коломбо
    199: "GOI",  # Гоа
    2783: "DPS",  # Бали (Денпасар)
    556: "HKT",  # Пхукет
    346: "BKK",  # Бангкок
}

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
    Парсит команду, поддерживает МНОГО направлений.
    Пример: "билеты Бали Гоа Шри-Ланка 18.05-25.05"
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

    # 1. Даты
    date_range = parse_date_range(text_lower)
    if date_range:
        params["departure_date"] = date_range[0]
        params["return_date"] = date_range[1]
    else:
        # Ищем одиночную дату или месяц
        for word in text_lower.split():
            if '.' in word:
                d = parse_date(word)
                if d:
                    params["departure_date"] = d
                    break
            if word in MONTH_MAPPING and not params["month"]:
                params["month"] = MONTH_MAPPING[word]

    # 2. Города (Origins / Destinations)
    # Собираем ВСЕ города, которые нашли в тексте
    found_cities = []

    # Сначала проверяем статический маппинг (самое быстрое)
    # Сортируем ключи по длине, чтобы "Шри-Ланка" нашлась раньше "Шри"
    sorted_keys = sorted(CITY_MAPPING.keys(), key=len, reverse=True)

    # Удаляем из текста найденные даты, чтобы они не мешали
    clean_text = text_lower
    if params["departure_date"]:
        # Упрощенная очистка, можно улучшить
        pass

    for city_key in sorted_keys:
        if city_key in clean_text:
            # Чтобы не добавлять "Гоа" дважды, если он встречается 2 раза
            if not any(c["name"] == city_key for c in found_cities):
                found_cities.append({"name": city_key})
                # Убираем найденный город из текста, чтобы не найти "Ланка" после "Шри-Ланка"
                clean_text = clean_text.replace(city_key, "")

    # Логика распределения (Москва по дефолту)
    if not found_cities:
        params["origins"] = [{"name": "москва"}]
    else:
        # Если первый город Москва/Питер - считаем его Origin, остальные Destination
        first_city = found_cities[0]["name"]
        if first_city in ["москва", "мск", "питер", "спб", "екатеринбург"]:
            params["origins"] = [found_cities[0]]
            params["destinations"] = found_cities[1:]
        else:
            # Иначе считаем, что Origin = Москва, а всё, что нашли - Destinations
            params["origins"] = [{"name": "москва"}]
            params["destinations"] = found_cities

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


def select_best_tickets(tickets: List[Dict], count: int = 2) -> List[Dict]:
    """
    Выбирает лучшие билеты по совокупности факторов (цена, пересадки, время).
    Не просто самые дешевые!
    """
    scored_tickets = []

    for t in tickets:
        # 1. Извлекаем числовые значения
        price = t["price"]
        stops = t["stops"]

        # Парсим длительность "15ч 30м" -> 15.5
        duration_hours = 0
        try:
            dur_str = t["duration"]
            parts = dur_str.split('ч')
            h = int(parts[0]) if parts[0].isdigit() else 0
            m = 0
            if len(parts) > 1 and 'м' in parts[1]:
                m = int(parts[1].replace('м', '').strip())
            duration_hours = h + (m / 60)
        except Exception:
            duration_hours = 24

        # 2. СЧИТАЕМ РЕЙТИНГ (меньше = лучше)
        # База = Цена
        score = price

        # Штраф за пересадки: каждая пересадка "стоит" как +3000 руб
        score += stops * 3000

        # Штраф за длительность: каждый лишний час "стоит" как +300 руб
        # (Сравниваем с условным минимумом 8 часов)
        if duration_hours > 8:
            score += (duration_hours - 8) * 300

        t["smart_score"] = score
        scored_tickets.append(t)

    # Сортируем по рейтингу (самые выгодные/удобные сверху)
    scored_tickets.sort(key=lambda x: x["smart_score"])

    return scored_tickets[:count]


async def search_tickets_smart(
    origin_name: str,
    dest_name: str,
    dep_date: str,
    ret_date: Optional[str],
    passengers: int
) -> List[Dict]:
    """
    Обертка над поиском: ищет, генерирует ссылку, выбирает лучшие.
    """
    origin_id = await resolve_city_id(origin_name)
    destination_id = await resolve_city_id(dest_name)

    if not origin_id or not destination_id:
        return []

    # 1. Запрос API (Поиск по самой низкой цене)
    offers = await fetch_offers(origin_id, destination_id, dep_date, ret_date, passengers)

    if not offers:
        return []

    # 2. Ссылка
    try:
        from_code = STATIC_IATA.get(origin_id, str(origin_id))
        to_code = STATIC_IATA.get(destination_id, str(destination_id))

        d_dt = datetime.strptime(dep_date, "%Y-%m-%d")
        d_str = d_dt.strftime("%d%m%Y")

        link = (
            "https://avia.tutu.ru/offers/?"
            f"passengers={passengers}&class=Y&route[0]={origin_id}-{d_str}-{destination_id}&changes=all"
        )

        if ret_date:
            r_dt = datetime.strptime(ret_date, "%Y-%m-%d")
            r_str = r_dt.strftime("%d%m%Y")
            link += f"&route[1]={destination_id}-{r_str}-{origin_id}"

        _ = from_code, to_code
    except Exception:
        link = "https://avia.tutu.ru/"

    # 3. Парсинг
    parsed = []
    for o in offers:
        t = parse_offer(o)
        if t and t["price"] > 0:
            t["deeplink"] = link
            parsed.append(t)

    # 4. Умный выбор
    return select_best_tickets(parsed, count=3)

async def search_tickets(
    origin_name: str,
    destination_name: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
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
    Обрабатывает сложные запросы: "билеты Бали Гоа 10.05"
    """
    try:
        params = parse_search_command(message.text)

        origins = params["origins"]
        destinations = params["destinations"]
        dep_date = params["departure_date"]
        ret_date = params["return_date"]
        month = params["month"]

        if not destinations:
            await message.reply("🌏 Куда летим? Укажите город (Бали, Гоа, Мальдивы...)")
            return

        status_msg = await message.reply("🔍 Начинаю поиск билетов...")

        final_results = []

        # --- СЦЕНАРИЙ 1: Поиск по ДАТАМ (для каждого направления) ---
        if dep_date:
            dates_info = dep_date
            if ret_date:
                dates_info += f" - {ret_date}"

            await status_msg.edit_text(
                f"🗓 Ищу билеты на {dates_info} по {len(destinations)} направлениям..."
            )

            for dest in destinations:
                res = await search_tickets_smart(
                    origins[0]["name"], dest["name"], dep_date, ret_date, params["passengers"]
                )
                if res:
                    # Добавляем заголовок направления в первый билет
                    res[0]["is_header"] = True
                    res[0]["dest_title"] = dest["name"].upper()
                    final_results.extend(res)
                await asyncio.sleep(1)

        # --- СЦЕНАРИЙ 2: Поиск по МЕСЯЦУ (Сканирование) ---
        elif month:
            # Берем текущий год (или следующий)
            now = datetime.now()
            year = now.year
            if month < now.month:
                year += 1

            # Сканируем выборочные даты (например, каждые 4 дня), чтобы было быстро
            # Или каждые выходные. Для простоты берем 5, 12, 19, 26 числа
            scan_days = [5, 12, 19, 26]
            scan_dates = []
            for d in scan_days:
                try:
                    dt = datetime(year, month, d)
                    if dt > now:
                        scan_dates.append(dt.strftime("%Y-%m-%d"))
                except Exception:
                    pass

            await status_msg.edit_text(
                f"📅 Сканирую {len(destinations)} направлений на месяц ({len(scan_dates)} дат)...\n"
                f"Это займет время."
            )

            for dest in destinations:
                best_for_dest = []
                for date in scan_dates:
                    res = await search_tickets_smart(
                        origins[0]["name"], dest["name"], date, None, params["passengers"]
                    )
                    best_for_dest.extend(res)
                    await asyncio.sleep(0.5)

                # Выбираем ТОП-3 из всего месяца для этого направления
                best_for_dest = select_best_tickets(best_for_dest, count=3)
                if best_for_dest:
                    best_for_dest[0]["is_header"] = True
                    best_for_dest[0]["dest_title"] = f"{dest['name'].upper()} (Лучшие в месяце)"
                    final_results.extend(best_for_dest)

        # --- ВЫВОД РЕЗУЛЬТАТОВ ---
        if not final_results:
            await status_msg.edit_text("😕 Ничего интересного не нашел.")
            return

        await status_msg.delete()

        # Формируем красивый отчет
        lines = []
        for t in final_results:
            if t.get("is_header"):
                lines.append(f"\n🌴 <b>{t['dest_title']}</b>")

            icon = "✈️" if t["stops"] == 0 else "🔄"
            price_fmt = f"{t['price']:,}".replace(",", " ")

            # Ссылка уже содержит правильную дату
            lines.append(
                f"{icon} <a href='{t['deeplink']}'>{t['departure']}</a> | {t['airline']}\n"
                f"   ⏳ {t['duration']} | {price_fmt} ₽"
            )

        # Разбиваем на сообщения, если слишком длинно
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "... (много вариантов)"

        await message.reply(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Global Error: {e}", exc_info=True)
        await message.reply("❌ Ошибка поиска.")
