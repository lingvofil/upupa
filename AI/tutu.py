#tutu.py

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import httpx
from aiogram import types
import json

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

TUTU_BASE_URL = "https://avia.tutu.ru"
TUTU_API_BASE = "https://offers-api.tutu.ru/avia"

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

# Маппинг городов на IATA-коды
CITY_MAPPING = {
    "москва": "MOW",
    "мск": "MOW",
    "питер": "LED",
    "санкт-петербург": "LED",
    "спб": "LED",
    "екатеринбург": "SVX",
    "казань": "KZN",
    "сочи": "AER",
    "новосибирск": "OVB",
    "владивосток": "VVO",
    "калининград": "KGD",
    "краснодар": "KRR",
    "самара": "KUF",
    "уфа": "UFA",
    "ростов": "ROV",
    "ростов-на-дону": "ROV",
    "пермь": "PEE",
    "красноярск": "KJA",
    "воронеж": "VOZ",
    "волгоград": "VOG",
    "минск": "MSQ",
    "киев": "IEV",
    "алматы": "ALA",
    "ташкент": "TAS",
    "баку": "GYD",
    "ереван": "EVN",
    "тбилиси": "TBS",
    # Зарубежные направления
    "париж": "PAR",
    "лондон": "LON",
    "берлин": "BER",
    "рим": "ROM",
    "мадрид": "MAD",
    "барселона": "BCN",
    "стамбул": "IST",
    "дубай": "DXB",
    "нью-йорк": "NYC",
    "пекин": "BJS",
    "токио": "TYO",
    "сеул": "SEL",
    "бангкок": "BKK",
    "пхукет": "HKT",
    "паттайя": "BKK",  # Ближайший к Паттайе
    "гоа": "GOI",
    "дели": "DEL",
    "мумбаи": "BOM",
    "тель-авив": "TLV",
    "каир": "CAI",
    "дубровник": "DBV",
    "прага": "PRG",
    "варшава": "WAW",
    "будапешт": "BUD",
    "вена": "VIE",
    "амстердам": "AMS",
    "брюссель": "BRU",
    "копенгаген": "CPH",
    "стокгольм": "STO",
    "хельсинки": "HEL",
    "осло": "OSL",
    "афины": "ATH",
    "лиссабон": "LIS",
    "милан": "MIL",
    "венеция": "VCE",
    "флоренция": "FLR",
    "ницца": "NCE",
    "женева": "GVA",
    "цюрих": "ZRH",
}

# Эвристики для AI
AIRLINE_RATINGS = {
    "Аэрофлот": {"quality": 8, "reliability": 9},
    "S7 Airlines": {"quality": 8, "reliability": 8},
    "Уральские авиалинии": {"quality": 7, "reliability": 8},
    "Победа": {"quality": 5, "reliability": 7},
    "Utair": {"quality": 6, "reliability": 7},
    "Red Wings": {"quality": 6, "reliability": 7},
    "Nordstar": {"quality": 7, "reliability": 7},
    "Smartavia": {"quality": 6, "reliability": 7},
    "Turkish Airlines": {"quality": 9, "reliability": 9},
    "Emirates": {"quality": 10, "reliability": 10},
    "Qatar Airways": {"quality": 10, "reliability": 10},
    "Lufthansa": {"quality": 9, "reliability": 9},
    "Air France": {"quality": 8, "reliability": 8},
    "KLM": {"quality": 8, "reliability": 9},
}


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
        (r'(\d{1,2})\.(\d{1,2})', lambda m: None),  # Обрабатываем отдельно
    ]
    
    for pattern, formatter in patterns:
        match = re.search(pattern, date_str)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                # Только день и месяц
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
        "origins": [{"code": "MOW", "name": "москва"}],
        "destinations": [{"code": "AER", "name": "сочи"}, ...],
        "departure_date": "2026-05-18" или None,
        "return_date": "2026-05-25" или None,
        "month": 5 или None,
        "passengers": 1
    }
    """
    text_lower = text.lower().strip()
    
    # Убираем префикс команды
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
        logging.info(f"Найдены даты туда-обратно: {date_range[0]} - {date_range[1]}")
    else:
        # Ищем одну дату
        for word in text_lower.split():
            if '.' in word:
                date = parse_date(word)
                if date:
                    params["departure_date"] = date
                    break
    
    # 2. Поиск месяца (если нет точных дат)
    if not params["departure_date"]:
        for word in text_lower.split():
            if word in MONTH_MAPPING:
                params["month"] = MONTH_MAPPING[word]
                break
    
    # 3. Поиск ВСЕХ городов в тексте
    found_cities = []
    for city_name, code in CITY_MAPPING.items():
        if city_name in text_lower:
            if not any(c["code"] == code for c in found_cities):
                found_cities.append({
                    "code": code,
                    "name": city_name
                })
    
    # 4. Определяем origins и destinations
    if not found_cities:
        # Нет городов → Москва-завтра (дефолт)
        params["origins"] = [{"code": "MOW", "name": "москва"}]
    elif len(found_cities) == 1:
        # Один город → Москва-Город
        params["origins"] = [{"code": "MOW", "name": "москва"}]
        params["destinations"] = found_cities
    elif len(found_cities) == 2:
        # Два города → Город1-Город2
        params["origins"] = [found_cities[0]]
        params["destinations"] = [found_cities[1]]
    else:
        # 3+ города → Москва-(Город1, Город2, ...)
        params["origins"] = [{"code": "MOW", "name": "москва"}]
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


async def create_session(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
) -> Optional[str]:
    """
    ЭТАП 1: Создает сессию поиска в Tutu.ru
    
    Returns: sessionId или None
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://avia.tutu.ru/",
            "Origin": "https://avia.tutu.ru",
        }
        
        payload = {
            "route": {
                "segments": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "date": departure_date
                    }
                ]
            },
            "passengers": {
                "adults": passengers,
                "children": 0,
                "infants": 0
            },
            "serviceClass": "economy"
        }
        
        # Если туда-обратно
        if return_date:
            payload["route"]["segments"].append({
                "origin": destination,
                "destination": origin,
                "date": return_date
            })
        
        async with httpx.AsyncClient(timeout=30.0, http2=True) as client:
            response = await client.post(
                f"{TUTU_BASE_URL}/session",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                session_id = data.get("sessionId")
                if session_id:
                    logging.info(f"Создана сессия: {session_id}")
                    return session_id
            
            logging.error(f"Ошибка создания сессии: {response.status_code}")
            return None
            
    except Exception as e:
        logging.error(f"Ошибка в create_session: {e}")
        return None


async def fetch_offers(session_id: str, max_wait: int = 60) -> Optional[Dict]:
    """
    ЭТАП 2: Получает предложения по сессии (polling)
    
    Returns: {"dictionary": {...}, "offers": [...]} или None
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://avia.tutu.ru/",
        }
        
        url = f"{TUTU_API_BASE}/offers?sessionId={session_id}"
        
        start_time = datetime.now()
        dictionary = None
        all_offers = []
        
        async with httpx.AsyncClient(timeout=30.0, http2=True) as client:
            while (datetime.now() - start_time).total_seconds() < max_wait:
                response = await client.get(url, headers=headers)
                
                if response.status_code != 200:
                    logging.error(f"Ошибка получения оферов: {response.status_code}")
                    await asyncio.sleep(2)
                    continue
                
                data = response.json()
                
                # Получаем словари (приходят в первом чанке)
                if not dictionary and data.get("dictionary"):
                    dictionary = data["dictionary"]
                    logging.info("Получен dictionary")
                
                # Собираем офферы
                if data.get("offers"):
                    all_offers.extend(data["offers"])
                
                # Проверяем завершение поиска
                if data.get("search_completed") or data.get("searchCompleted"):
                    logging.info(f"Поиск завершен. Найдено {len(all_offers)} оферов")
                    break
                
                await asyncio.sleep(2)
        
        if dictionary and all_offers:
            return {
                "dictionary": dictionary,
                "offers": all_offers
            }
        
        logging.warning("Не удалось получить полные данные")
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в fetch_offers: {e}")
        return None


def map_ticket_data(offer: Dict, dictionary: Dict) -> Optional[Dict]:
    """
    ЭТАП 3: Маппинг данных из offer и dictionary
    
    Returns: полная информация о билете
    """
    try:
        ticket = {
            "price": 0,
            "currency": "RUB",
            "origin": "",
            "destination": "",
            "airline": "Неизвестно",
            "operated_by": None,
            "departure_time": "",
            "arrival_time": "",
            "duration_minutes": 0,
            "stops_count": 0,
            "stops_duration_minutes": 0,
            "stops_cities": [],
            "baggage": "Неизвестно",
            "hand_luggage": "Неизвестно",
            "is_multi_pnr": False,
            "link": ""
        }
        
        # Цена
        ticket["price"] = offer.get("price", {}).get("amount", 0)
        ticket["currency"] = offer.get("price", {}).get("currency", "RUB")
        
        # Сегменты
        segment_ids = offer.get("segmentIds", [])
        if not segment_ids:
            return None
        
        segments = dictionary.get("avia", {}).get("segments", {})
        
        total_duration = 0
        all_stops = []
        
        for seg_id in segment_ids:
            segment = segments.get(seg_id)
            if not segment:
                continue
            
            # Время вылета/прилета
            if not ticket["departure_time"]:
                ticket["departure_time"] = segment.get("departureTime", "")
            ticket["arrival_time"] = segment.get("arrivalTime", "")
            
            # Аэропорты
            origin_code = segment.get("origin")
            dest_code = segment.get("destination")
            
            airports = dictionary.get("common", {}).get("airports", {})
            
            if not ticket["origin"] and origin_code:
                airport = airports.get(origin_code, {})
                ticket["origin"] = airport.get("city", {}).get("name", origin_code)
            
            if dest_code:
                airport = airports.get(dest_code, {})
                ticket["destination"] = airport.get("city", {}).get("name", dest_code)
            
            # Длительность
            duration = segment.get("durationMinutes", 0)
            total_duration += duration
            
            # Пересадки
            if segment.get("connectionTime"):
                all_stops.append({
                    "city": ticket["destination"],
                    "duration": segment["connectionTime"]
                })
        
        ticket["duration_minutes"] = total_duration
        ticket["stops_count"] = len(all_stops)
        
        if all_stops:
            ticket["stops_duration_minutes"] = sum(s["duration"] for s in all_stops)
            ticket["stops_cities"] = [s["city"] for s in all_stops]
        
        # Авиакомпания
        carrier_id = offer.get("carrierId")
        if carrier_id:
            carriers = dictionary.get("common", {}).get("carriers", {})
            carrier = carriers.get(carrier_id, {})
            ticket["airline"] = carrier.get("name", "Неизвестно")
        
        # Багаж
        fare_id = offer.get("fareApplicationId")
        if fare_id:
            conditions = dictionary.get("avia", {}).get("conditions", {})
            fare = conditions.get(fare_id, {})
            
            baggage_info = fare.get("baggage", {})
            if baggage_info.get("included"):
                weight = baggage_info.get("weight", 0)
                ticket["baggage"] = f"{weight} кг" if weight else "Включен"
            else:
                ticket["baggage"] = "Без багажа"
            
            hand_luggage = fare.get("handLuggage", {})
            if hand_luggage.get("included"):
                weight = hand_luggage.get("weight", 0)
                ticket["hand_luggage"] = f"{weight} кг" if weight else "Включена"
        
        # MultiPNR
        ticket["is_multi_pnr"] = offer.get("isMultiPnr", False)
        
        # Ссылка
        ticket["link"] = f"{TUTU_BASE_URL}/booking/{offer.get('id', '')}"
        
        return ticket
        
    except Exception as e:
        logging.error(f"Ошибка в map_ticket_data: {e}")
        return None


def format_duration(minutes: int) -> str:
    """Форматирует длительность в читабельный вид."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}ч {mins}м" if mins else f"{hours}ч"


async def analyze_tickets_with_ai(tickets: List[Dict], params: Dict) -> List[Dict]:
    """
    AI-анализ билетов с контекстом и рекомендациями.
    
    Использует Groq для генерации комментариев к каждому билету.
    """
    if not tickets or len(tickets) == 0:
        return []
    
    # Сортируем по цене
    tickets.sort(key=lambda x: x.get("price", 0))
    
    # Берем топ-20 для анализа
    candidates = tickets[:20]
    
    # Статистика
    prices = [t["price"] for t in candidates]
    durations = [t["duration_minutes"] for t in candidates]
    
    avg_price = int(sum(prices) / len(prices))
    min_price = min(prices)
    max_price = max(prices)
    
    avg_duration = int(sum(durations) / len(durations))
    
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
• Средняя длительность: {format_duration(avg_duration)}

КАНДИДАТЫ (топ-20 билетов):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

ЗАДАЧА:
Выбери ТОП-7 билетов по разным сценариям:
1. Минимальный бюджет (но адекватные условия)
2. Лучший баланс цена/время
3. Прямой рейс (если есть)
4. С багажом
5. Премиум авиакомпания
6-7. Дополнительные интересные варианты

КРИТЕРИИ:
• Учитывай длительность пересадок (4+ часа - плохо)
• MultiPNR (раздельные билеты) - серьезный риск, обязательно укажи
• Авиакомпании: Аэрофлот, S7, Turkish - надежно; лоукостеры - дешево, но риски
• Багаж: важно для многих, отметь наличие/отсутствие
• Все цены указывай в РУБЛЯХ

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON массив из 7 объектов:
[
  {{
    "index": 0,
    "ai_score": 9,
    "scenario": "Минимальный бюджет",
    "reason": "S7 Airlines, 15 200 ₽. Без багажа, но прямой рейс 3ч 20м. Отличный вариант для налегке."
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
    
    # Простая сортировка по value score
    for ticket in candidates:
        duration_hours = ticket["duration_minutes"] / 60
        price_per_hour = ticket["price"] / max(duration_hours, 1)
        
        # Штрафы
        penalty = 0
        if ticket["stops_count"] > 0:
            penalty += ticket["stops_count"] * 1000
        if ticket["stops_duration_minutes"] > 120:
            penalty += 2000
        if ticket["is_multi_pnr"]:
            penalty += 5000
        
        ticket['value_score'] = 100000 - ticket["price"] - penalty
    
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
        link = ticket.get("link", "#")
        airline = ticket.get("airline", "Неизвестно")
        
        lines.append(f"<b>{i}. <a href='{link}'>{airline}</a></b>")
        
        if ticket.get("scenario"):
            lines.append(f"🎯 <i>{ticket['scenario']}</i>")
        
        # Время и длительность
        dep_time = ticket.get("departure_time", "")
        arr_time = ticket.get("arrival_time", "")
        duration = format_duration(ticket.get("duration_minutes", 0))
        
        if dep_time and arr_time:
            # Извлекаем только время (HH:MM)
            dep_time_short = dep_time.split("T")[1][:5] if "T" in dep_time else dep_time[:5]
            arr_time_short = arr_time.split("T")[1][:5] if "T" in arr_time else arr_time[:5]
            lines.append(f"🕒 {dep_time_short} → {arr_time_short} ({duration})")
        
        # Пересадки
        stops = ticket.get("stops_count", 0)
        if stops == 0:
            lines.append("✈️ Прямой рейс")
        else:
            stops_dur = format_duration(ticket.get("stops_duration_minutes", 0))
            stops_cities = ", ".join(ticket.get("stops_cities", []))
            lines.append(f"🔄 {stops} пересадка: {stops_cities} ({stops_dur})")
        
        # Багаж
        baggage = ticket.get("baggage", "")
        hand_luggage = ticket.get("hand_luggage", "")
        
        baggage_line = ""
        if baggage and baggage != "Неизвестно":
            baggage_line += f"🧳 {baggage}"
        if hand_luggage and hand_luggage != "Неизвестно":
            if baggage_line:
                baggage_line += f" | ✋ {hand_luggage}"
            else:
                baggage_line += f"✋ {hand_luggage}"
        
        if baggage_line:
            lines.append(baggage_line)
        
        # MultiPNR предупреждение
        if ticket.get("is_multi_pnr"):
            lines.append("⚠️ <b>Раздельные билеты!</b> Риск при пересадке")
        
        # AI комментарий
        if ticket.get("ai_reason"):
            lines.append(f"🤖 <i>{ticket['ai_reason']}</i>")
        
        # Цена
        price = ticket.get("price", 0)
        currency = ticket.get("currency", "RUB")
        symbol = "₽" if currency == "RUB" else currency
        
        lines.append(f"💰 <b>{price:,} {symbol}</b>\n")
    
    return "\n".join(lines)


async def search_tickets(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1
) -> List[Dict]:
    """
    Полный цикл поиска билетов для одного направления.
    
    Returns: список билетов
    """
    logging.info(f"Поиск билетов: {origin} → {destination}, {departure_date}")
    
    # Этап 1: Создаем сессию
    session_id = await create_session(origin, destination, departure_date, return_date, passengers)
    if not session_id:
        logging.error("Не удалось создать сессию")
        return []
    
    # Этап 2: Получаем оферы
    data = await fetch_offers(session_id)
    if not data:
        logging.error("Не удалось получить оферы")
        return []
    
    dictionary = data["dictionary"]
    offers = data["offers"]
    
    # Этап 3: Маппинг данных
    tickets = []
    for offer in offers:
        ticket = map_ticket_data(offer, dictionary)
        if ticket and ticket["price"] > 0:
            tickets.append(ticket)
    
    logging.info(f"Найдено {len(tickets)} билетов")
    return tickets


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
                origin["code"],
                destination["code"],
                departure_date,
                return_date,
                passengers
            )
            
            # Добавляем метаданные
            for ticket in tickets:
                ticket["search_origin"] = origin["name"]
                ticket["search_destination"] = destination["name"]
            
            all_tickets.extend(tickets)
            
            # Задержка между запросами
            await asyncio.sleep(2)
    
    return all_tickets


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
            
            # Генерируем даты месяца
            dates = generate_month_dates(month)
            
            all_tickets = []
            for date in dates[:10]:  # Ограничиваем 10 датами для скорости
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
