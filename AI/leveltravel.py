import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from playwright.async_api import async_playwright
from aiogram import types
import json
import httpx  # Для прямых API запросов

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID


# =============================================================================
# КОНСТАНТЫ И НАСТРОЙКИ
# =============================================================================

LEVELTRAVEL_BASE_URL = "https://level.travel"

# Маппинг направлений на коды для Level.Travel
# Формат: "название": ("код_страны", "код_курорта/региона")
COUNTRY_MAPPING = {
    "северный гоа": ("in", "north-goa"),
    "гоа": ("in", "north-goa"),  # по умолчанию северный
    "мальдивы": ("mv", None),
    "шри-ланка": ("lk", None),
    "шриланка": ("lk", None),
    "вьетнам": ("vn", None),
    "фукуок": ("vn", "phu-quoc"),
    "нячанг": ("vn", "nha-trang"),
    "нячянг": ("vn", "nha-trang"),  # альтернативное написание
    "турция": ("tr", None),
    "бали": ("id", "bali"),
    "индонезия": ("id", "bali"),  # по умолчанию Бали
}

# Город вылета по умолчанию
DEFAULT_DEPARTURE_CITY = "moscow"  # Москва

# Эвристики по направлениям
DESTINATION_INFO = {
    "north-goa": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3],  # Ноябрь-март
        "sea_temp_ok": [11, 12, 1, 2, 3, 4],
        "description": "тусовочное место с пляжами и ночной жизнью"
    },
    "mv": {  # Мальдивы
        "party": False,
        "best_months": [11, 12, 1, 2, 3, 4],
        "sea_temp_ok": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "description": "спокойный пляжный отдых, романтика"
    },
    "lk": {  # Шри-Ланка
        "party": False,
        "best_months": [12, 1, 2, 3, 4],
        "sea_temp_ok": [11, 12, 1, 2, 3, 4, 5],
        "description": "пляжи, культура, природа"
    },
    "vn": {  # Вьетнам
        "party": True,
        "best_months": [11, 12, 1, 2, 3, 4],
        "sea_temp_ok": [1, 2, 3, 4, 5, 11, 12],
        "description": "разнообразный отдых"
    },
    "phu-quoc": {
        "party": False,
        "best_months": [11, 12, 1, 2, 3, 4],
        "sea_temp_ok": [11, 12, 1, 2, 3, 4, 5],
        "description": "тихие пляжи, природа"
    },
    "nha-trang": {
        "party": True,
        "best_months": [1, 2, 3, 4, 5],
        "sea_temp_ok": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "description": "активный пляжный отдых, развлечения"
    },
    "tr": {  # Турция
        "party": True,
        "best_months": [5, 6, 7, 8, 9, 10],
        "sea_temp_ok": [5, 6, 7, 8, 9, 10],
        "description": "all inclusive, пляжи, инфраструктура"
    },
    "bali": {
        "party": True,
        "best_months": [4, 5, 6, 7, 8, 9, 10],
        "sea_temp_ok": [4, 5, 6, 7, 8, 9, 10, 11],
        "description": "серфинг, тусовки, культура"
    },
}

# Маппинг месяцев
MONTH_MAPPING = {
    "январь": 1, "января": 1, "янв": 1,
    "февраль": 2, "февраля": 2, "фев": 2,
    "март": 3, "марта": 3, "мар": 3,
    "апрель": 4, "апреля": 4, "апр": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6, "июн": 6,
    "июль": 7, "июля": 7, "июл": 7,
    "август": 8, "августа": 8, "авг": 8,
    "сентябрь": 9, "сентября": 9, "сен": 9, "сент": 9,
    "октябрь": 10, "октября": 10, "окт": 10,
    "ноябрь": 11, "ноября": 11, "ноя": 11,
    "декабрь": 12, "декабря": 12, "дек": 12,
}


# =============================================================================
# ПАРСИНГ ПАРАМЕТРОВ ИЗ КОМАНДЫ
# =============================================================================

def parse_tour_command(text: str) -> Dict:
    """
    Парсит команду типа "туры апрель шри-ланка 2"
    
    Returns:
        Dict с параметрами: month, country, country_code, resort, adults, nights_from, nights_to
    """
    text_lower = text.lower().strip()
    
    # Убираем команду "туры"
    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    
    params = {
        "month": None,
        "country": None,
        "country_code": None,
        "resort": None,
        "adults": 2,  # по умолчанию
        "nights_from": 7,  # по умолчанию минимум ночей
        "nights_to": 14,  # по умолчанию максимум ночей
        "departure_city": DEFAULT_DEPARTURE_CITY,
    }
    
    words = text_lower.split()
    
    # Ищем месяц
    for word in words:
        if word in MONTH_MAPPING:
            params["month"] = MONTH_MAPPING[word]
            break
    
    # Ищем направление (может быть составное название вроде "северный гоа")
    for destination_name, (country_code, resort_code) in COUNTRY_MAPPING.items():
        if destination_name in text_lower:
            params["country_code"] = country_code
            params["resort"] = resort_code
            params["country_name"] = destination_name
            break
    
    # Ищем количество взрослых (одиночные цифры от 1 до 9)
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])
    
    # Ищем продолжительность (например, "7-10 ночей" или "10 ночей")
    nights_patterns = [
        r'(\d+)-(\d+)\s*(?:ночей|ночи|ночь)',  # "7-10 ночей"
        r'(\d+)\s*(?:ночей|ночи|ночь)',  # "10 ночей"
    ]
    
    for pattern in nights_patterns:
        match = re.search(pattern, text_lower)
        if match:
            if len(match.groups()) == 2:
                params["nights_from"] = int(match.group(1))
                params["nights_to"] = int(match.group(2))
            else:
                nights = int(match.group(1))
                params["nights_from"] = max(nights - 2, 5)
                params["nights_to"] = nights + 2
            break
    
    return params


def generate_date_range(month: Optional[int] = None) -> List[str]:
    """
    Генерирует список дат для поиска.
    Если месяц указан - все даты месяца, иначе - следующие 60 дней.
    
    Returns:
        List дат в формате YYYY-MM-DD
    """
    dates = []
    today = datetime.now()
    
    if month:
        # Находим год для этого месяца
        current_month = today.month
        year = today.year if month >= current_month else today.year + 1
        
        # Генерируем все даты месяца
        from calendar import monthrange
        _, last_day = monthrange(year, month)
        
        for day in range(1, last_day + 1):
            date = datetime(year, month, day)
            if date >= today:  # только будущие даты
                dates.append(date.strftime("%Y-%m-%d"))
    else:
        # Следующие 60 дней
        for i in range(60):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%Y-%m-%d"))
    
    return dates


# =============================================================================
# ПАРСИНГ LEVEL.TRAVEL
# =============================================================================

# =============================================================================
# ПРЯМОЙ API ЗАПРОС (НОВЫЙ МЕТОД)
# =============================================================================

async def direct_api_search(
    country_code: str,
    date: str,
    adults: int = 2,
    nights_from: int = 7,
    nights_to: int = 14,
    departure_city: str = "moscow"
) -> List[Dict]:
    """
    Прямой запрос к API Level.Travel (без Playwright)
    
    Args:
        country_code: код страны
        date: дата вылета YYYY-MM-DD
        adults: количество взрослых
        nights_from: минимум ночей
        nights_to: максимум ночей
        departure_city: город вылета
    
    Returns:
        List туров
    """
    tours = []
    
    try:
        # API endpoint Level.Travel (может потребоваться уточнение)
        # Это примерный URL, реальный может отличаться
        api_url = "https://api.level.travel/search/start"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Referer': 'https://level.travel/',
            'Origin': 'https://level.travel'
        }
        
        payload = {
            "country": country_code.upper(),
            "from_city": departure_city,
            "start_date": date,
            "adults": adults,
            "nights_min": nights_from,
            "nights_max": nights_to,
            "currency": "rub"
        }
        
        logging.info(f"Прямой API запрос к Level.Travel: {api_url}")
        logging.info(f"Параметры: {payload}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_url, json=payload, headers=headers)
            
            logging.info(f"API ответ: status={response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logging.info(f"Получен JSON от API")
                
                # Пробуем найти туры в разных ключах
                tours_data = None
                if isinstance(data, list):
                    tours_data = data
                elif isinstance(data, dict):
                    for key in ['tours', 'offers', 'hotels', 'results', 'data', 'items']:
                        if key in data:
                            tours_data = data[key]
                            break
                
                if tours_data and isinstance(tours_data, list):
                    logging.info(f"Найдено туров в API: {len(tours_data)}")
                    
                    for item in tours_data:
                        tour = parse_tour_from_api(item)
                        if tour and tour.get('price', 0) > 10000:
                            tours.append(tour)
                else:
                    logging.warning(f"Структура API ответа неожиданная. Ключи: {list(data.keys()) if isinstance(data, dict) else 'not dict'}")
            else:
                logging.error(f"API вернул ошибку: {response.status_code} - {response.text[:200]}")
                
    except Exception as e:
        logging.error(f"Ошибка прямого API запроса: {e}")
    
    return tours


def parse_tour_from_api(item: Dict) -> Optional[Dict]:
    """
    Парсит тур из JSON ответа API
    
    Args:
        item: элемент из API ответа
    
    Returns:
        Dict с данными тура или None
    """
    try:
        tour = {
            'hotel_name': '',
            'price': 0,
            'currency': 'RUB',
            'rating': 0,
            'reviews_count': 0,
            'location': '',
            'stars': 0,
            'url': '',
            'departure_date': '',
            'nights': 0,
            'meal_type': '',
        }
        
        # Название отеля
        for key in ['hotel_name', 'hotelName', 'name', 'title', 'hotel']:
            if key in item and item[key]:
                tour['hotel_name'] = str(item[key])
                break
        
        # Цена
        for key in ['price', 'cost', 'total_price', 'totalPrice', 'amount']:
            if key in item and item[key]:
                try:
                    tour['price'] = int(float(item[key]))
                    break
                except:
                    pass
        
        # Рейтинг
        for key in ['rating', 'hotel_rating', 'hotelRating', 'stars_rating']:
            if key in item and item[key]:
                try:
                    tour['rating'] = float(item[key])
                    break
                except:
                    pass
        
        # Отзывы
        for key in ['reviews', 'reviews_count', 'reviewsCount']:
            if key in item and item[key]:
                try:
                    tour['reviews_count'] = int(item[key])
                    break
                except:
                    pass
        
        # Звёзды
        for key in ['stars', 'hotel_stars', 'hotelStars', 'star']:
            if key in item and item[key]:
                try:
                    tour['stars'] = int(float(item[key]))
                    break
                except:
                    pass
        
        # Локация
        for key in ['location', 'city', 'region', 'resort']:
            if key in item and item[key]:
                tour['location'] = str(item[key])
                break
        
        # URL
        for key in ['url', 'link', 'href', 'tour_url']:
            if key in item and item[key]:
                url = str(item[key])
                if not url.startswith('http'):
                    url = LEVELTRAVEL_BASE_URL + url
                tour['url'] = url
                break
        
        # Дата
        for key in ['departure_date', 'departureDate', 'date', 'start_date']:
            if key in item and item[key]:
                tour['departure_date'] = str(item[key])
                break
        
        # Ночи
        for key in ['nights', 'duration', 'nights_count']:
            if key in item and item[key]:
                try:
                    tour['nights'] = int(item[key])
                    break
                except:
                    pass
        
        # Питание
        for key in ['meal', 'meal_type', 'mealType', 'food']:
            if key in item and item[key]:
                tour['meal_type'] = str(item[key])
                break
        
        return tour if tour['hotel_name'] or tour['location'] else None
        
    except Exception as e:
        logging.warning(f"Ошибка парсинга тура из API: {e}")
        return None


# =============================================================================
# ПАРСИНГ LEVEL.TRAVEL ЧЕРЕЗ PLAYWRIGHT
# =============================================================================

async def scrape_leveltravel(
    country_code: str,
    dates: List[str],
    adults: int = 2,
    nights_from: int = 7,
    nights_to: int = 14,
    resort: Optional[str] = None,
    departure_city: str = "moscow",
    max_results: int = 50
) -> List[Dict]:
    """
    Скрапит туры с Level.Travel через перехват API запросов
    
    Args:
        country_code: код страны (например, "lk" для Шри-Ланки)
        dates: список дат вылета (используется первая)
        adults: количество взрослых
        nights_from: минимум ночей
        nights_to: максимум ночей
        resort: код курорта/региона (например, "north-goa")
        departure_city: город вылета (по умолчанию "moscow")
        max_results: максимальное количество результатов
    
    Returns:
        List словарей с информацией о турах
    """
    tours = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
            )
            
            page = await context.new_page()
            
            # Список для сохранения API ответов
            api_responses = []
            
            # Перехватчик API запросов (ИСПРАВЛЕННЫЙ)
            async def handle_response(response):
                try:
                    url = response.url.lower()
                    
                    # ❌ Игнорируем Sentry и аналитику
                    if any(x in url for x in ['sentry', 'metrics', 'analytics', 'gtag', 'google', 'facebook']):
                        return
                    
                    # ✅ Реальные API Level.Travel
                    if any(x in url for x in [
                        'level.travel/api',
                        'b2c-api.level.travel',
                        '/searches',
                        '/offers',
                        '/hotels'
                    ]) and response.status == 200:
                        
                        ct = response.headers.get('content-type', '')
                        if 'json' not in ct:
                            return
                        
                        data = await response.json()
                        api_responses.append({
                            'url': response.url,
                            'data': data
                        })
                        
                        logging.info(f"🔥 API TOUR DATA: {response.url}")
                        
                except Exception as e:
                    logging.debug(f"API parse skip: {e}")
            
            page.on('response', handle_response)
            
            try:
                # СТРАТЕГИЯ: идём на главную, триггерим JS-поиск, ждём API
                logging.info(f"Открываю Level.Travel главную страницу")
                
                await page.goto(LEVELTRAVEL_BASE_URL, timeout=60000, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)
                
                # Триггерим JS-событие поиска (работает на Level.Travel)
                logging.info("Триггерим поиск через JS event")
                await page.evaluate("""
                    () => {
                        const ev = new Event('search', { bubbles: true });
                        window.dispatchEvent(ev);
                    }
                """)
                
                # ЖДЁМ API запрос с турами (правильный синтаксис для Playwright Python)
                logging.info("Ожидаем API ответ с турами...")
                try:
                    async with page.expect_response(
                        lambda response: (
                            'level.travel' in response.url.lower() and
                            any(x in response.url.lower() for x in ['offers', 'searches', 'hotels']) and
                            response.status == 200
                        ),
                        timeout=20000
                    ) as response_info:
                        # Ждём пока придёт нужный ответ
                        await page.wait_for_timeout(1000)
                    
                    response = await response_info.value
                    logging.info(f"✅ Поймали API ответ: {response.url}")
                except Exception as e:
                    logging.warning(f"⚠️ Не дождались API с турами за 20 сек: {e}")
                
                # Даём время на обработку ответа
                await page.wait_for_timeout(3000)
                
            except Exception as e:
                logging.warning(f"Ошибка при работе с главной: {e}")
                
                # Fallback: прямая ссылка (но она скорее всего не сработает)
                search_params = [
                    f"country={country_code.upper()}",
                    f"from={departure_city}",
                    f"adults={adults}",
                    f"nights_from={nights_from}",
                    f"nights_to={nights_to}"
                ]
                
                if dates:
                    search_params.append(f"date={dates[0]}")
                
                search_url = f"{LEVELTRAVEL_BASE_URL}/search?{'&'.join(search_params)}"
                logging.info(f"Fallback: переход на {search_url}")
                
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                # Пробуем триггернуть поиск и там
                try:
                    await page.evaluate("""
                        () => {
                            const ev = new Event('search', { bubbles: true });
                            window.dispatchEvent(ev);
                        }
                    """)
                    
                    # Ждём API (правильный синтаксис)
                    async with page.expect_response(
                        lambda response: (
                            'level.travel' in response.url.lower() and
                            any(x in response.url.lower() for x in ['offers', 'searches']) and
                            response.status == 200
                        ),
                        timeout=20000
                    ) as response_info:
                        await page.wait_for_timeout(1000)
                except Exception:
                    logging.warning("Fallback тоже не сработал")
                
                await page.wait_for_timeout(3000)
                
                # Дополнительно скроллим для триггера lazy load
                for _ in range(3):
                    await page.evaluate('window.scrollBy(0, 1000)')
                    await page.wait_for_timeout(1000)
                
                # Теперь парсим данные из перехваченных API ответов
                logging.info(f"Перехвачено API запросов: {len(api_responses)}")
                
                # ОТЛАДКА: логируем все URL
                if api_responses:
                    logging.info("Перехваченные URL:")
                    for resp in api_responses:
                        logging.info(f"  - {resp['url']}")
                
                if api_responses:
                    # Ищем ответ с турами
                    for api_resp in api_responses:
                        data = api_resp.get('data', {})
                        
                        # Пробуем разные структуры данных
                        tours_data = None
                        
                        if isinstance(data, list):
                            tours_data = data
                        elif isinstance(data, dict):
                            # Пробуем разные ключи
                            for key in ['tours', 'offers', 'hotels', 'results', 'data', 'items']:
                                if key in data:
                                    tours_data = data[key]
                                    break
                        
                        if tours_data and isinstance(tours_data, list):
                            logging.info(f"Найдены туры в API ответе: {len(tours_data)} шт.")
                            
                            # Парсим данные из API
                            for item in tours_data[:max_results]:
                                try:
                                    tour = {
                                        'hotel_name': '',
                                        'price': 0,
                                        'currency': 'RUB',
                                        'rating': 0,
                                        'reviews_count': 0,
                                        'location': '',
                                        'stars': 0,
                                        'url': '',
                                        'departure_date': '',
                                        'nights': 0,
                                        'meal_type': '',
                                    }
                                    
                                    # Название отеля
                                    for key in ['hotel_name', 'hotelName', 'name', 'title', 'hotel']:
                                        if key in item and item[key]:
                                            tour['hotel_name'] = str(item[key])
                                            break
                                    
                                    # Цена
                                    for key in ['price', 'cost', 'total_price', 'totalPrice', 'amount']:
                                        if key in item and item[key]:
                                            try:
                                                tour['price'] = int(float(item[key]))
                                                break
                                            except:
                                                pass
                                    
                                    # Рейтинг
                                    for key in ['rating', 'hotel_rating', 'hotelRating', 'stars_rating']:
                                        if key in item and item[key]:
                                            try:
                                                tour['rating'] = float(item[key])
                                                break
                                            except:
                                                pass
                                    
                                    # Отзывы
                                    for key in ['reviews', 'reviews_count', 'reviewsCount']:
                                        if key in item and item[key]:
                                            try:
                                                tour['reviews_count'] = int(item[key])
                                                break
                                            except:
                                                pass
                                    
                                    # Звёзды отеля
                                    for key in ['stars', 'hotel_stars', 'hotelStars', 'star']:
                                        if key in item and item[key]:
                                            try:
                                                tour['stars'] = int(float(item[key]))
                                                break
                                            except:
                                                pass
                                    
                                    # Локация
                                    for key in ['location', 'city', 'region', 'resort']:
                                        if key in item and item[key]:
                                            tour['location'] = str(item[key])
                                            break
                                    
                                    # URL
                                    for key in ['url', 'link', 'href', 'tour_url']:
                                        if key in item and item[key]:
                                            url = str(item[key])
                                            if not url.startswith('http'):
                                                url = LEVELTRAVEL_BASE_URL + url
                                            tour['url'] = url
                                            break
                                    
                                    # Дата вылета
                                    for key in ['departure_date', 'departureDate', 'date', 'start_date']:
                                        if key in item and item[key]:
                                            tour['departure_date'] = str(item[key])
                                            break
                                    
                                    # Ночи
                                    for key in ['nights', 'duration', 'nights_count']:
                                        if key in item and item[key]:
                                            try:
                                                tour['nights'] = int(item[key])
                                                break
                                            except:
                                                pass
                                    
                                    # Питание
                                    for key in ['meal', 'meal_type', 'mealType', 'food']:
                                        if key in item and item[key]:
                                            tour['meal_type'] = str(item[key])
                                            break
                                    
                                    # Добавляем если есть минимальные данные
                                    if (tour['hotel_name'] or tour['location']) and tour['price'] > 10000:
                                        tours.append(tour)
                                        
                                except Exception as e:
                                    logging.warning(f"Ошибка парсинга тура из API: {e}")
                                    continue
                            
                            if tours:
                                break  # Нашли туры, выходим
                
                # Если API не дал результатов, пробуем парсить DOM (как fallback)
                if not tours:
                    logging.info("API не дал результатов, пробуем парсить DOM...")
                    
                    tours_data = await page.evaluate("""
                        () => {
                            let results = [];
                            
                            // Ищем карточки туров
                            const selectors = [
                                '[data-testid*="tour"]',
                                '[data-testid*="offer"]',
                                '[class*="TourCard"]',
                                '[class*="OfferCard"]',
                                'article',
                            ];
                            
                            let cards = [];
                            for (const selector of selectors) {
                                const elements = document.querySelectorAll(selector);
                                if (elements.length > 0) {
                                    cards = Array.from(elements);
                                    break;
                                }
                            }
                            
                            // Fallback: любые div с ценой
                            if (cards.length === 0) {
                                cards = Array.from(document.querySelectorAll('div')).filter(div => {
                                    const text = div.textContent || '';
                                    return /\d{4,7}\s*₽/.test(text) && div.querySelectorAll('*').length > 3;
                                });
                            }
                            
                            cards.forEach((card, index) => {
                                try {
                                    const allText = card.textContent || '';
                                    
                                    const tour = {
                                        index: index,
                                        hotel_name: '',
                                        price: 0,
                                        rating: 0,
                                        reviews_count: 0,
                                        stars: 0,
                                        nights: 0,
                                        location: '',
                                        departure_date: '',
                                        meal_type: '',
                                        url: ''
                                    };
                                    
                                    // Название
                                    const nameEl = card.querySelector('h2, h3, h4, [class*="name"]');
                                    if (nameEl) tour.hotel_name = nameEl.textContent.trim();
                                    
                                    // Цена
                                    const priceMatch = allText.match(/(\d{4,7})\s*₽/);
                                    if (priceMatch) tour.price = parseInt(priceMatch[1]);
                                    
                                    // Рейтинг
                                    const ratingMatch = allText.match(/(\d\.?\d?)\s*\/\s*10/);
                                    if (ratingMatch) tour.rating = parseFloat(ratingMatch[1]);
                                    
                                    // Отзывы
                                    const reviewMatch = allText.match(/(\d+)\s*отзыв/i);
                                    if (reviewMatch) tour.reviews_count = parseInt(reviewMatch[1]);
                                    
                                    // Звёзды
                                    const starsMatch = allText.match(/(\d)\s*(?:звезд|★)/i);
                                    if (starsMatch) tour.stars = parseInt(starsMatch[1]);
                                    
                                    // Ночи
                                    const nightsMatch = allText.match(/(\d+)\s*(?:ночей|ночи)/i);
                                    if (nightsMatch) tour.nights = parseInt(nightsMatch[1]);
                                    
                                    // Ссылка
                                    const linkEl = card.querySelector('a[href]');
                                    if (linkEl) {
                                        tour.url = linkEl.getAttribute('href');
                                        if (tour.url && !tour.url.startsWith('http')) {
                                            tour.url = 'https://level.travel' + tour.url;
                                        }
                                    }
                                    
                                    if ((tour.hotel_name || tour.location) && tour.price > 10000) {
                                        results.push(tour);
                                    }
                                } catch (e) {}
                            });
                            
                            return results;
                        }
                    """)
                    
                    tours.extend(tours_data)
                
                logging.info(f"Найдено туров (итого): {len(tours)}")
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка при скрапинге Level.Travel: {e}")
    
    return tours[:max_results]


# =============================================================================
# АНАЛИЗ ТУРОВ ЧЕРЕЗ GROQ
# =============================================================================

async def analyze_tours_with_groq(tours: List[Dict], params: Dict) -> List[Dict]:
    """
    Анализирует туры через Groq и выбирает самые релевантные
    
    Args:
        tours: список туров
        params: параметры поиска
    
    Returns:
        Отсортированный список лучших туров (до 10 штук)
    """
    if not tours:
        return []
    
    # ПРЕДФИЛЬТРАЦИЯ на Python
    filtered_tours = []
    for tour in tours:
        # Базовые фильтры
        if tour.get('price', 0) < 10000:  # Слишком дешево = подозрительно
            continue
        
        # Фильтр по рейтингу ИЛИ отзывам (если есть данные)
        has_good_rating = tour.get('rating', 0) >= 4.0
        has_reviews = tour.get('reviews_count', 0) >= 10
        
        # Пропускаем только если есть явно плохой рейтинг
        if tour.get('rating', 0) > 0 and tour.get('rating') < 3.5:
            continue
        
        filtered_tours.append(tour)
    
    if not filtered_tours:
        # Если после фильтрации ничего не осталось, берём исходные
        filtered_tours = tours
    
    logging.info(f"После предфильтрации осталось {len(filtered_tours)} туров из {len(tours)}")
    
    # Получаем информацию о направлении
    destination_key = params.get('resort') or params.get('country_code')
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    month_name = None
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    country_name = params.get("country_name", "неизвестная страна")
    
    # Формируем улучшенный промпт с эвристиками
    season_info = ""
    if params.get("month"):
        month_num = params["month"]
        best_months = destination_meta.get('best_months', [])
        sea_ok = destination_meta.get('sea_temp_ok', [])
        
        if month_num in best_months:
            season_info = f"✅ {month_name.capitalize()} - ОТЛИЧНЫЙ сезон для {country_name}"
        elif month_num in sea_ok:
            season_info = f"⚠️ {month_name.capitalize()} - приемлемый сезон, но не идеальный"
        else:
            season_info = f"❌ {month_name.capitalize()} - НЕ сезон для {country_name} (дожди/холодно)"
    
    party_info = ""
    if destination_meta.get('party'):
        party_info = "✅ Место ТУСОВОЧНОЕ - есть ночная жизнь, бары, развлечения"
    else:
        party_info = "⚠️ Место СПОКОЙНОЕ - больше для релакса и романтики"
    
    dest_description = destination_meta.get('description', '')
    
    prompt = f"""Ты - эксперт по турам. Проанализируй туры в {country_name.capitalize()} и выбери ТОП-10.

КОНТЕКСТ НАПРАВЛЕНИЯ:
{dest_description}
{season_info}
{party_info}

КРИТЕРИИ ОТБОРА (по важности):
1. **Сезонность и погода** - комфортно ли купаться в указанный период
2. **Рейтинг и отзывы** - чем выше, тем лучше (но учти, что у многих туров рейтинга нет)
3. **Инфраструктура** - {party_info.split('-')[1].strip() if '-' in party_info else 'развлечения'}
4. **Цена/качество** - оптимальное соотношение
5. **Звёздность отеля** - предпочтение 4-5 звёздам

ВАЖНО:
- У многих туров рейтинг = 0 (данных нет) - это НОРМАЛЬНО, не штрафуй за это
- Кондиционеры есть почти везде в тёплых странах, это не критично
- Если месяц не в сезон - честно скажи об этом в reason

СПИСОК ТУРОВ:
{json.dumps(filtered_tours[:30], ensure_ascii=False, indent=2)}

ФОРМАТ ОТВЕТА - СТРОГО JSON (массив):
[
  {{
    "index": <индекс из списка>,
    "score": <оценка 1-10>,
    "reason": "<почему этот вариант хорош: сезон, цена, звёзды, локация - 1-2 предложения>"
  }}
]

Верни ТОЛЬКО JSON массив, без markdown и комментариев."""

    try:
        # Вызываем Groq для анализа
        response = await groq_ai.generate_text(prompt, temperature=0.3)
        
        # Парсим JSON из ответа
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            logging.error("Groq не вернул валидный JSON")
            # Возвращаем топ по цене
            sorted_tours = sorted(filtered_tours, key=lambda x: x.get('price', 999999))
            return sorted_tours[:10]
        
        analysis_results = json.loads(json_match.group(0))
        
        # Сортируем результаты по score
        analysis_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Формируем финальный список туров с анализом
        analyzed_tours = []
        for result in analysis_results[:10]:
            index = result.get("index", 0)
            if 0 <= index < len(filtered_tours):
                tour = filtered_tours[index].copy()
                tour["ai_score"] = result.get("score", 0)
                tour["ai_reason"] = result.get("reason", "")
                analyzed_tours.append(tour)
        
        return analyzed_tours
        
    except Exception as e:
        logging.error(f"Ошибка при анализе туров через Groq: {e}")
        # Возвращаем топ по рейтингу или цене
        sorted_tours = sorted(
            filtered_tours,
            key=lambda x: (x.get('rating', 0), -x.get('price', 999999)),
            reverse=True
        )
        return sorted_tours[:10]


# =============================================================================
# ФОРМАТИРОВАНИЕ РЕЗУЛЬТАТОВ
# =============================================================================

def format_tours_message(tours: List[Dict], params: Dict) -> str:
    """
    Форматирует список туров в читаемое сообщение
    
    Args:
        tours: список туров
        params: параметры поиска
    
    Returns:
        Отформатированное сообщение
    """
    if not tours:
        return "😢 К сожалению, не удалось найти подходящие туры по вашим критериям."
    
    country_name = params.get("country_name", "выбранное направление")
    month_name = None
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    header = f"🏖 <b>Топ-{len(tours)} туров: {country_name.capitalize()}</b>"
    if month_name:
        header += f" <b>({month_name})</b>"
    header += f"\n👥 {params['adults']} взрослых | 🌙 {params['nights_from']}-{params['nights_to']} ночей | ✈️ из Москвы\n"
    
    lines = [header]
    
    for i, tour in enumerate(tours, 1):
        lines.append(f"\n<b>{i}. {tour.get('hotel_name', tour.get('location', 'Отель'))}</b>")
        
        # Основная информация
        details = []
        
        if tour.get('price'):
            details.append(f"💰 {tour['price']:,} ₽")
        
        if tour.get('stars'):
            details.append(f"⭐️ {'★' * tour['stars']}")
        
        if tour.get('rating') and tour['rating'] > 0:
            details.append(f"📊 {tour['rating']}/10")
        
        if tour.get('reviews_count') and tour['reviews_count'] > 0:
            details.append(f"💬 {tour['reviews_count']} отзывов")
        
        if tour.get('location'):
            details.append(f"📍 {tour['location']}")
        
        if tour.get('nights'):
            details.append(f"🌙 {tour['nights']} ночей")
        
        if tour.get('meal_type'):
            details.append(f"🍽 {tour['meal_type']}")
        
        if tour.get('departure_date'):
            details.append(f"📅 {tour['departure_date']}")
        
        if details:
            lines.append(" | ".join(details))
        
        # AI анализ
        if tour.get('ai_score'):
            lines.append(f"🤖 Оценка AI: {tour['ai_score']}/10")
        
        if tour.get('ai_reason'):
            lines.append(f"💡 {tour['ai_reason']}")
        
        # Ссылка
        if tour.get('url'):
            lines.append(f"🔗 <a href='{tour['url']}'>Подробнее на Level.Travel</a>")
    
    return "\n".join(lines)


# =============================================================================
# ГЛАВНАЯ ФУНКЦИЯ-ОРКЕСТРАТОР
# =============================================================================

async def process_tours_command(message: types.Message):
    """
    Главная функция для обработки команды "туры"
    
    Args:
        message: сообщение от пользователя
    """
    # Проверка прав доступа
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 Эта команда доступна только администратору.")
        return
    
    try:
        # Парсим параметры из команды
        params = parse_tour_command(message.text)
        
        # Проверяем, что направление указано
        if not params.get("country_code"):
            await message.reply(
                "❌ Пожалуйста, укажите направление. Доступные варианты:\n\n"
                "🇮🇳 <b>Северный Гоа</b>: <code>туры северный гоа</code>\n"
                "🇲🇻 <b>Мальдивы</b>: <code>туры мальдивы</code>\n"
                "🇱🇰 <b>Шри-Ланка</b>: <code>туры шри-ланка</code>\n"
                "🇻🇳 <b>Вьетнам</b>: <code>туры вьетнам</code>\n"
                "🏝 <b>Фукуок</b>: <code>туры фукуок</code>\n"
                "🏖 <b>Нячанг</b>: <code>туры нячанг</code>\n"
                "🇹🇷 <b>Турция</b>: <code>туры турция</code>\n"
                "🌴 <b>Бали</b>: <code>туры бали</code>\n\n"
                "Примеры полных команд:\n"
                "<code>туры апрель северный гоа 2</code>\n"
                "<code>туры май шри-ланка</code>\n"
                "<code>туры фукуок 4</code>",
                parse_mode="HTML"
            )
            return
        
        # Генерируем даты для поиска
        dates = generate_date_range(params.get("month"))
        
        # Отправляем сообщение о начале поиска
        search_msg = await message.reply(
            f"🔍 Ищу туры: {params.get('country_name', 'выбранное направление').title()}\n"
            f"👥 {params['adults']} взрослых | 🌙 {params['nights_from']}-{params['nights_to']} ночей\n"
            f"✈️ Вылет из Москвы\n"
            f"Это может занять некоторое время ⏳"
        )
        
        tours = []
        
        # МЕТОД 1: Прямой API запрос (быстрее и надёжнее)
        try:
            logging.info("Пробуем прямой API запрос...")
            if dates:
                tours = await direct_api_search(
                    country_code=params["country_code"],
                    date=dates[0],
                    adults=params["adults"],
                    nights_from=params["nights_from"],
                    nights_to=params["nights_to"],
                    departure_city=params["departure_city"]
                )
                
                if tours:
                    logging.info(f"✅ Прямой API дал результат: {len(tours)} туров")
        except Exception as e:
            logging.error(f"Прямой API не сработал: {e}")
        
        # МЕТОД 2: Playwright (если API не дал результата)
        if not tours:
            logging.info("Прямой API не дал результатов, пробуем Playwright...")
            tours = await scrape_leveltravel(
                country_code=params["country_code"],
                dates=dates,
                adults=params["adults"],
                nights_from=params["nights_from"],
                nights_to=params["nights_to"],
                resort=params.get("resort"),
                departure_city=params["departure_city"],
                max_results=50
            )
        
        if not tours:
            await search_msg.edit_text(
                "😕 Не удалось найти туры. Возможно:\n"
                "• Сайт изменил структуру API\n"
                "• Нет доступных предложений по заданным параметрам\n"
                "• Требуется другой метод поиска\n\n"
                "💡 Попробуйте:\n"
                "- Изменить даты (другой месяц)\n"
                "- Изменить продолжительность\n"
                "- Выбрать другое направление"
            )
            return
        
        await search_msg.edit_text(
            f"✅ Найдено {len(tours)} туров!\n"
            f"🤖 Анализирую варианты через AI..."
        )
        
        # Анализируем туры через Groq
        best_tours = await analyze_tours_with_groq(tours, params)
        
        # Форматируем и отправляем результаты
        result_message = format_tours_message(best_tours, params)
        
        await search_msg.delete()
        await message.reply(result_message, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Ошибка в process_tours_command: {e}")
        await message.reply(
            f"❌ Произошла ошибка при поиске туров:\n<code>{str(e)}</code>",
            parse_mode="HTML"
        )
