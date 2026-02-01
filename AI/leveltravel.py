import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from aiogram import types
import json
import httpx

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

LEVELTRAVEL_BASE_URL = "https://api.level.travel"
LEVELTRAVEL_WEB_URL = "https://level.travel"

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

# Place IDs для Level.Travel API
PLACE_ID_MAPPING = {
    "IN": 10088,      # Индия (Гоа)
    "MV": 10095,      # Мальдивы
    "LK": 10109,      # Шри-Ланка
    "VN": 10176,      # Вьетнам
    "TR": 10091,      # Турция
    "ID": 10085,      # Индонезия (Бали)
}

# Маппинг направлений
COUNTRY_MAPPING = {
    "северный гоа": ("IN", None),
    "гоа": ("IN", None),
    "мальдивы": ("MV", None),
    "шри-ланка": ("LK", None),
    "шриланка": ("LK", None),
    "вьетнам": ("VN", None),
    "фукуок": ("VN", None),
    "нячанг": ("VN", None),
    "турция": ("TR", None),
    "бали": ("ID", None),
    "индонезия": ("ID", None),
}

# API ключ (извлечён из запросов)
LEVELTRAVEL_API_KEY = "0fe9fb2ff35679322db5429b18a53aee"

# Эвристики
DESTINATION_INFO = {
    "IN": {"party": True, "best_months": [11, 12, 1, 2, 3], "description": "тусовки и пляжи"},
    "MV": {"party": False, "best_months": [11, 12, 1, 2, 3, 4], "description": "романтика"},
    "LK": {"party": False, "best_months": [12, 1, 2, 3, 4], "description": "пляжи и культура"},
    "VN": {"party": True, "best_months": [11, 12, 1, 2, 3, 4], "description": "разнообразие"},
    "TR": {"party": True, "best_months": [5, 6, 7, 8, 9, 10], "description": "all inclusive"},
    "ID": {"party": True, "best_months": [4, 5, 6, 7, 8, 9, 10], "description": "серфинг"},
}


def generate_date_range(month: Optional[int] = None) -> List[str]:
    """Генерирует даты для поиска"""
    dates = []
    today = datetime.now()
    
    if month:
        year = today.year if month >= today.month else today.year + 1
        # Ключевые даты: 1, 8, 15, 22 числа месяца
        for day in [1, 8, 15, 22]:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%Y-%m-%d"))
            except ValueError:
                pass
    else:
        # Ближайшие 30 дней с шагом 7
        for i in range(0, 30, 7):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%Y-%m-%d"))
    
    return dates


def parse_tour_command(text: str) -> Dict:
    """Парсит команду"""
    text_lower = text.lower().strip()
    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    
    params = {
        "month": None,
        "country_code": None,
        "adults": 2,
        "nights_from": 7,
        "nights_to": 14,
    }
    
    # Месяц
    for word in text_lower.split():
        if word in MONTH_MAPPING:
            params["month"] = MONTH_MAPPING[word]
            break
    
    # Направление
    for dest_name, (code, _) in COUNTRY_MAPPING.items():
        if dest_name in text_lower:
            params["country_code"] = code
            params["country_name"] = dest_name
            break
    
    # Взрослые
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])
    
    # Ночи
    nights_match = re.search(r'(\d+)\s*(?:ночей|ночи|ночь)', text_lower)
    if nights_match:
        nights = int(nights_match.group(1))
        params["nights_from"] = max(nights - 2, 5)
        params["nights_to"] = nights + 2
    
    return params


async def get_tours_from_leveltravel_api(
    place_id: int,
    date: str,
    adults: int,
    nights_from: int,
    nights_to: int,
    departure_id: int = 213  # Москва
) -> List[Dict]:
    """
    Получает туры через API Level.Travel
    
    Трёхступенчатый процесс:
    1. Создаём поиск (получаем request_id)
    2. Ждём готовности (polling status)
    3. Забираем результаты (get_grouped_hotels)
    """
    tours = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Шаг 1: Создаём request_id через search_calendar
            logging.info(f"Создаём поиск: place_id={place_id}, date={date}, adults={adults}")
            
            search_params = {
                "start_date": date,
                "place_id": place_id,
                "departure_id": departure_id,
                "adults": adults,
                "search_type": "package",
                "key": LEVELTRAVEL_API_KEY,
                "api_version": "3.13",
                "js": "true"
            }
            
            # Генерируем sign (простая версия - может потребоваться MD5)
            import hashlib
            params_str = "&".join(f"{k}={v}" for k, v in sorted(search_params.items()))
            search_params["sign"] = hashlib.md5(params_str.encode()).hexdigest()
            
            # Запрос на получение request_id
            search_url = f"{LEVELTRAVEL_BASE_URL}/references/search_calendar_duration"
            response = await client.get(search_url, params=search_params)
            
            logging.info(f"Search calendar response: {response.status_code}")
            
            if response.status_code != 200:
                logging.error(f"API error: {response.text[:500]}")
                return tours
            
            # Получаем request_id из куки или ответа
            # Level.Travel часто отдаёт request_id не в JSON, а как часть процесса
            # Попробуем альтернативный подход - прямой запрос к enqueue
            
            # Шаг 1 (альтернатива): Enqueue
            enqueue_params = {
                "start_date": date,
                "to_country": place_id,
                "from_city": departure_id,
                "adults": adults,
                "nights_min": nights_from,
                "nights_max": nights_to,
                "key": LEVELTRAVEL_API_KEY,
                "api_version": "3.13"
            }
            
            enqueue_url = f"{LEVELTRAVEL_BASE_URL}/search/enqueue"
            enqueue_response = await client.get(enqueue_url, params=enqueue_params)
            
            logging.info(f"Enqueue response: {enqueue_response.status_code}")
            
            if enqueue_response.status_code == 200:
                enqueue_data = enqueue_response.json()
                request_id = enqueue_data.get("request_id")
                
                if not request_id:
                    logging.warning("No request_id in enqueue response")
                    return tours
                
                logging.info(f"Got request_id: {request_id}")
                
                # Шаг 2: Polling - ждём готовности
                max_attempts = 20
                for attempt in range(max_attempts):
                    await asyncio.sleep(2)
                    
                    status_params = {
                        "request_id": request_id,
                        "show_size": "true",
                        "key": LEVELTRAVEL_API_KEY,
                        "api_version": "3.13"
                    }
                    
                    params_str = "&".join(f"{k}={v}" for k, v in sorted(status_params.items()))
                    status_params["sign"] = hashlib.md5(params_str.encode()).hexdigest()
                    
                    status_url = f"{LEVELTRAVEL_BASE_URL}/search/status"
                    status_response = await client.get(status_url, params=status_params)
                    
                    if status_response.status_code == 200:
                        status_data = status_response.json()
                        status = status_data.get("status")
                        
                        logging.info(f"Status check {attempt + 1}/{max_attempts}: {status}")
                        
                        if status == "finished" or status_data.get("size", 0) > 0:
                            break
                    
                    if attempt == max_attempts - 1:
                        logging.warning("Max polling attempts reached")
                        return tours
                
                # Шаг 3: Получаем отели
                hotels_params = {
                    "request_id": request_id,
                    "sort_by": "relevance",
                    "page_limit": 100,
                    "page_number": 0,
                    "key": LEVELTRAVEL_API_KEY,
                    "api_version": "3.13"
                }
                
                params_str = "&".join(f"{k}={v}" for k, v in sorted(hotels_params.items()))
                hotels_params["sign"] = hashlib.md5(params_str.encode()).hexdigest()
                
                hotels_url = f"{LEVELTRAVEL_BASE_URL}/search/get_grouped_hotels"
                hotels_response = await client.get(hotels_url, params=hotels_params)
                
                logging.info(f"Hotels response: {hotels_response.status_code}")
                
                if hotels_response.status_code == 200:
                    hotels_data = hotels_response.json()
                    
                    # Парсим структуру
                    hotels_list = hotels_data.get("hotels", [])
                    
                    if not hotels_list:
                        # Пробуем другие ключи
                        hotels_list = hotels_data.get("offers", [])
                    
                    if not hotels_list:
                        hotels_list = hotels_data.get("results", [])
                    
                    logging.info(f"Найдено отелей в API: {len(hotels_list)}")
                    
                    for hotel in hotels_list:
                        try:
                            tour = {
                                "hotel_name": hotel.get("hotel_name") or hotel.get("name", ""),
                                "price": int(hotel.get("price", 0) or hotel.get("min_price", 0)),
                                "rating": float(hotel.get("rating", 0)),
                                "reviews_count": int(hotel.get("reviews_count", 0)),
                                "stars": int(hotel.get("stars", 0)),
                                "location": hotel.get("location") or hotel.get("resort", ""),
                                "nights": int(hotel.get("nights", 0)),
                                "meal_type": hotel.get("meal_type") or hotel.get("meal", ""),
                                "url": f"{LEVELTRAVEL_WEB_URL}/hotel/{hotel.get('hotel_id', '')}" if hotel.get("hotel_id") else "",
                            }
                            
                            if tour["price"] > 10000:
                                tours.append(tour)
                        except Exception as e:
                            logging.warning(f"Error parsing hotel: {e}")
                            continue
                else:
                    logging.error(f"Hotels API error: {hotels_response.text[:500]}")
            else:
                logging.error(f"Enqueue error: {enqueue_response.text[:500]}")
                
    except Exception as e:
        logging.error(f"API request failed: {e}")
    
    return tours


async def search_tours_multi_date(
    country_code: str,
    dates: List[str],
    adults: int,
    nights_from: int,
    nights_to: int
) -> List[Dict]:
    """Поиск туров по нескольким датам с дедупликацией"""
    
    place_id = PLACE_ID_MAPPING.get(country_code)
    if not place_id:
        logging.error(f"Unknown country code: {country_code}")
        return []
    
    all_tours = []
    seen_hotels = set()
    
    # Берём максимум 3 даты для ускорения
    for date in dates[:3]:
        logging.info(f"Поиск на дату: {date}")
        
        tours = await get_tours_from_leveltravel_api(
            place_id=place_id,
            date=date,
            adults=adults,
            nights_from=nights_from,
            nights_to=nights_to
        )
        
        # Дедупликация по названию отеля
        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower()
            if hotel_key and hotel_key not in seen_hotels:
                seen_hotels.add(hotel_key)
                all_tours.append(tour)
        
        if len(all_tours) >= 30:
            break
    
    logging.info(f"Всего уникальных туров: {len(all_tours)}")
    return all_tours


async def analyze_tours_with_groq(tours: List[Dict], params: Dict) -> List[Dict]:
    """Анализ через Groq"""
    if not tours:
        return []
    
    # Предфильтрация
    filtered = [t for t in tours if t.get("price", 0) >= 10000]
    
    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    month_name = ""
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    season_info = ""
    if params.get("month"):
        month_num = params["month"]
        best_months = destination_meta.get("best_months", [])
        season_info = "✅ ОТЛИЧНЫЙ сезон" if month_num in best_months else "⚠️ Не лучший сезон"
    
    party_info = "✅ Тусовочное" if destination_meta.get("party") else "⚠️ Спокойное"
    
    prompt = f"""Выбери ТОП-10 туров для {params.get('country_name', '').capitalize()}.

КОНТЕКСТ:
{destination_meta.get('description')}
{season_info}
{party_info}

КРИТЕРИИ:
1. Сезонность
2. Рейтинг (у многих 0 - это норма)
3. Цена/качество
4. Звёзды 4-5

ТУРЫ:
{json.dumps(filtered[:30], ensure_ascii=False, indent=2)}

ОТВЕТ (JSON):
[
  {{"index": 0, "score": 8, "reason": "1-2 предложения"}},
  ...
]"""

    try:
        response = await groq_ai.generate_text(prompt, temperature=0.3)
        json_match = re.search(r'\[[\s\S]*\]', response)
        
        if json_match:
            analysis = json.loads(json_match.group(0))
            analysis.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            result = []
            for item in analysis[:10]:
                idx = item.get("index", 0)
                if 0 <= idx < len(filtered):
                    tour = filtered[idx].copy()
                    tour["ai_score"] = item.get("score", 0)
                    tour["ai_reason"] = item.get("reason", "")
                    result.append(tour)
            
            return result
    except Exception as e:
        logging.error(f"AI analysis error: {e}")
    
    # Fallback
    return sorted(filtered, key=lambda x: (x.get("rating", 0), -x.get("price", 999999)), reverse=True)[:10]


def format_tours_message(tours: List[Dict], params: Dict) -> str:
    """Форматирование"""
    if not tours:
        return "😢 Туры не найдены"
    
    country_name = params.get("country_name", "направление")
    
    header = f"🏖 <b>Топ-{len(tours)}: {country_name.capitalize()}</b>\n"
    header += f"👥 {params['adults']} взрослых | ✈️ из Москвы\n"
    
    lines = [header]
    
    for i, tour in enumerate(tours, 1):
        lines.append(f"\n<b>{i}. {tour.get('hotel_name', 'Отель')}</b>")
        
        details = []
        if tour.get("price"):
            details.append(f"💰 {tour['price']:,} ₽")
        if tour.get("stars"):
            details.append(f"⭐️ {'★' * tour['stars']}")
        if tour.get("rating") and tour["rating"] > 0:
            details.append(f"📊 {tour['rating']}/10")
        if tour.get("reviews_count"):
            details.append(f"💬 {tour['reviews_count']}")
        if tour.get("location"):
            details.append(f"📍 {tour['location']}")
        if tour.get("nights"):
            details.append(f"🌙 {tour['nights']} ночей")
        if tour.get("meal_type"):
            details.append(f"🍽 {tour['meal_type']}")
        
        if details:
            lines.append(" | ".join(details))
        
        if tour.get("ai_score"):
            lines.append(f"🤖 {tour['ai_score']}/10")
        if tour.get("ai_reason"):
            lines.append(f"💡 {tour['ai_reason']}")
        if tour.get("url"):
            lines.append(f"🔗 <a href='{tour['url']}'>Подробнее</a>")
    
    return "\n".join(lines)


async def process_tours_command(message: types.Message):
    """Главная функция"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 Только для администратора.")
        return
    
    try:
        params = parse_tour_command(message.text)
        
        if not params.get("country_code"):
            await message.reply(
                "❌ Укажите направление:\n\n"
                "🇮🇳 Гоа | 🇲🇻 Мальдивы | 🇱🇰 Шри-Ланка\n"
                "🇻🇳 Вьетнам | 🇹🇷 Турция | 🌴 Бали\n\n"
                "Пример: <code>туры апрель шри-ланка 2</code>",
                parse_mode="HTML"
            )
            return
        
        dates = generate_date_range(params.get("month"))
        
        search_msg = await message.reply(
            f"🔍 Ищу туры: {params.get('country_name', '').title()}\n"
            f"👥 {params['adults']} взрослых\n"
            f"Подождите 20-30 сек ⏳"
        )
        
        tours = await search_tours_multi_date(
            country_code=params["country_code"],
            dates=dates,
            adults=params["adults"],
            nights_from=params["nights_from"],
            nights_to=params["nights_to"]
        )
        
        if not tours:
            await search_msg.edit_text(
                "😕 Туры не найдены через API.\n\n"
                "Возможные причины:\n"
                "• API изменился\n"
                "• Нет туров на выбранные даты\n"
                "• Требуется обновление place_id"
            )
            return
        
        await search_msg.edit_text(f"✅ Найдено {len(tours)} туров!\n🤖 Анализирую...")
        
        best_tours = await analyze_tours_with_groq(tours, params)
        result = format_tours_message(best_tours, params)
        
        await search_msg.delete()
        await message.reply(result, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await message.reply(f"❌ Ошибка: {e}")
