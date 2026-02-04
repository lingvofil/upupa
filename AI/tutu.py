# tutu.py

import asyncio
import logging
import re
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

# Маппинг городов на CityId (из Tutu API)
CITY_MAPPING = {
    "москва": 491,
    "мск": 491,
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
    "минск": 509,
    "киев": 510,
    "алматы": 511,
    "ташкент": 512,
    "баку": 513,
    "ереван": 514,
    "тбилиси": 515,
    # Зарубежные направления
    "париж": 419,
    "лондон": 420,
    "берлин": 421,
    "рим": 422,
    "мадрид": 423,
    "барселона": 424,
    "стамбул": 387,
    "дубай": 425,
    "нью-йорк": 426,
    "пекин": 427,
    "токио": 428,
    "сеул": 429,
    "бангкок": 430,
    "пхукет": 431,
    "паттайя": 430,
    "гоа": 432,
    "дели": 433,
    "мумбаи": 434,
    "тель-авив": 435,
    "каир": 436,
    "дубровник": 437,
    "прага": 438,
    "варшава": 439,
    "будапешт": 440,
    "вена": 441,
    "амстердам": 442,
    "брюссель": 443,
    "копенгаген": 444,
    "стокгольм": 445,
    "хельсинки": 446,
    "осло": 447,
    "афины": 448,
    "лиссабон": 449,
    "милан": 450,
    "венеция": 451,
    "флоренция": 452,
    "ницца": 453,
    "женева": 454,
    "цюрих": 455,
}

# Обратный маппинг для форматирования
CITY_ID_TO_NAME = {v: k for k, v in CITY_MAPPING.items()}


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
        "origins": [{"id": 491, "name": "москва"}],
        "destinations": [{"id": 461, "name": "сочи"}, ...],
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
    
    # 3. Поиск ВСЕХ городов
    found_cities = []
    for city_name, city_id in CITY_MAPPING.items():
        if city_name in text_lower:
            if not any(c["id"] == city_id for c in found_cities):
                found_cities.append({
                    "id": city_id,
                    "name": city_name
                })
    
    # 4. Определяем origins и destinations
    if not found_cities:
        params["origins"] = [{"id": 491, "name": "москва"}]
    elif len(found_cities) == 1:
        params["origins"] = [{"id": 491, "name": "москва"}]
        params["destinations"] = found_cities
    elif len(found_cities) == 2:
        params["origins"] = [found_cities[0]]
        params["destinations"] = [found_cities[1]]
    else:
        params["origins"] = [{"id": 491, "name": "москва"}]
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
        
        payload = {
            "passengers": {
                "full": passengers,
                "child": 0,
                "infant": 0
            },
            "serviceClass": "Y",
            "routes": routes
        }
        
        logging.info(f"Запрос: {CITY_ID_TO_NAME.get(origin_id, origin_id)} → {CITY_ID_TO_NAME.get(destination_id, destination_id)}, {departure_date}")
        
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
                    data = data[0]  # Берем первый элемент списка
                
                if not isinstance(data, dict):
                    logging.error(f"Неожиданный тип ответа: {type(data)}")
                    return []
                
                # Офферы находятся в offers.actual
                offers_dict = data.get("offers", {})
                if isinstance(offers_dict, dict):
                    offers = offers_dict.get("actual", {})
                else:
                    logging.error(f"Неожиданная структура offers: {type(offers_dict)}")
                    return []
                
                if not offers:
                    logging.warning("Офферы не найдены в ответе")
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
    Парсит один оффер в упрощенный формат.
    
    Структура Tutu API:
    - offer содержит segmentIds, price, fareApplicationId
    - segments находятся в dictionary.avia.segments
    - carriers в dictionary.common.carriers
    - fare conditions в dictionary.avia.conditions
    
    Returns:
    {
        "price": int,
        "currency": str,
        "airline": str,
        "departure": str (ISO datetime),
        "arrival": str (ISO datetime),
        "duration": str (формат "3ч 20м"),
        "stops": int,
        "baggage": bool,
        "deeplink": str
    }
    """
    try:
        if not isinstance(offer, dict):
            logging.error(f"Оффер не является словарем: {type(offer)}")
            return None
        
        result = {
            "price": 0,
            "currency": "RUB",
            "airline": "Неизвестно",
            "departure": "",
            "arrival": "",
            "duration": "",
            "stops": 0,
            "baggage": False,
            "deeplink": ""
        }
        
        # Dictionary для резолва ID
        dictionary = offer.get("_dictionary", {})
        
        # Цена
        price_data = offer.get("price", {})
        if isinstance(price_data, dict):
            result["price"] = int(price_data.get("amount", 0))
            result["currency"] = price_data.get("currency", "RUB")
        elif isinstance(price_data, (int, float)):
            result["price"] = int(price_data)
        
        # Получаем сегменты по ID
        segment_ids = offer.get("segmentIds", [])
        if not segment_ids:
            logging.debug("Нет segmentIds в оффере")
            return None
        
        avia_dict = dictionary.get("avia", {})
        segments_dict = avia_dict.get("segments", {})
        
        if not segments_dict:
            logging.debug("Нет segments в dictionary")
            return None
        
        # Собираем сегменты
        segments = []
        for seg_id in segment_ids:
            segment = segments_dict.get(seg_id)
            if segment:
                segments.append(segment)
        
        if not segments:
            logging.debug("Не удалось найти сегменты")
            return None
        
        first_segment = segments[0]
        last_segment = segments[-1]
        
        # Время вылета/прилета
        result["departure"] = first_segment.get("departureTime", "")
        result["arrival"] = last_segment.get("arrivalTime", "")
        
        # Длительность
        total_duration = sum(s.get("durationMinutes", 0) for s in segments)
        hours = total_duration // 60
        minutes = total_duration % 60
        result["duration"] = f"{hours}ч {minutes}м" if minutes else f"{hours}ч"
        
        # Пересадки (количество сегментов - 1)
        result["stops"] = len(segments) - 1
        
        # Авиакомпания из первого сегмента
        carrier_id = first_segment.get("carrier")
        if carrier_id:
            common_dict = dictionary.get("common", {})
            carriers_dict = common_dict.get("carriers", {})
            carrier = carriers_dict.get(carrier_id, {})
            result["airline"] = carrier.get("name", "Неизвестно")
        
        # Багаж из fare conditions
        fare_id = offer.get("fareApplicationId")
        if fare_id:
            conditions_dict = avia_dict.get("conditions", {})
            fare = conditions_dict.get(fare_id, {})
            
            baggage_info = fare.get("baggage", {})
            if isinstance(baggage_info, dict):
                result["baggage"] = baggage_info.get("included", False)
            elif isinstance(baggage_info, bool):
                result["baggage"] = baggage_info
        
        # Ссылка для бронирования
        offer_id = offer.get("id", "")
        result["deeplink"] = f"https://avia.tutu.ru/booking/{offer_id}" if offer_id else ""
        
        return result
        
    except Exception as e:
        logging.error(f"Ошибка парсинга оффера: {e}", exc_info=True)
        return None


async def search_tickets(
    origin_id: int,
    destination_id: int,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
) -> List[Dict]:
    """
    Полный цикл поиска билетов для одного направления.
    
    Returns: список билетов (max 7)
    """
    offers = await fetch_offers(origin_id, destination_id, departure_date, return_date, passengers)
    
    if not offers:
        return []
    
    # Парсим офферы
    tickets = []
    for offer in offers:
        ticket = parse_offer(offer)
        if ticket and ticket["price"] > 0:
            tickets.append(ticket)
    
    # Сортируем по цене
    tickets.sort(key=lambda x: x["price"])
    
    # Ограничиваем 7 офферами
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
                origin["id"],
                destination["id"],
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
