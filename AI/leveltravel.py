import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from playwright.async_api import async_playwright
from aiogram import types
from aiogram.types import FSInputFile
import json
import os

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

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

# Маппинг направлений
COUNTRY_MAPPING = {
    "северный гоа": "IN",
    "гоа": "IN",
    "мальдивы": "MV",
    "шри-ланка": "LK",
    "шриланка": "LK",
    "вьетнам": "VN",
    "фукуок": "VN",
    "нячанг": "VN",
    "турция": "TR",
    "бали": "ID",
    "индонезия": "ID",
    "таиланд": "TH",
    "пхукет": "TH",
    "паттайя": "TH",
    "оаэ": "AE",
    "дубай": "AE",
    "египет": "EG",
    "хургада": "EG",
    "шарм": "EG",
}

# Эвристики для AI анализа
DESTINATION_INFO = {
    "IN": {"party": True, "best_months": [11, 12, 1, 2, 3], "description": "тусовки, свобода, пляжи"},
    "MV": {"party": False, "best_months": [11, 12, 1, 2, 3, 4], "description": "романтика, релакс, океан"},
    "LK": {"party": False, "best_months": [12, 1, 2, 3, 4], "description": "природа, серфинг, культура"},
    "VN": {"party": True, "best_months": [11, 12, 1, 2, 3, 4], "description": "еда, экскурсии, море"},
    "TR": {"party": True, "best_months": [5, 6, 7, 8, 9, 10], "description": "all inclusive, сервис"},
    "ID": {"party": True, "best_months": [4, 5, 6, 7, 8, 9, 10], "description": "джунгли, серфинг, атмосфера"},
    "TH": {"party": True, "best_months": [11, 12, 1, 2, 3, 4], "description": "ночная жизнь, острова, фрукты"},
    "AE": {"party": False, "best_months": [10, 11, 12, 3, 4], "description": "небоскребы, шопинг, пляжи"},
    "EG": {"party": False, "best_months": [4, 5, 9, 10, 11], "description": "дайвинг, пустыня, история"},
}


def generate_full_month_dates(month: Optional[int] = None) -> List[str]:
    """Генерирует ВСЕ возможные даты вылета для месяца."""
    dates = []
    today = datetime.now()
    
    if month:
        # Определяем год
        year = today.year if month >= today.month else today.year + 1
        
        # Генерируем все даты месяца
        day = 1
        while True:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%d.%m.%Y"))
                day += 1
            except ValueError:
                # Месяц закончился
                break
    else:
        # Если месяц не указан, берем следующие 30 дней
        for i in range(1, 31):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%d.%m.%Y"))
    
    return dates


def parse_tour_command(text: str) -> Dict:
    """Парсит текст команды от пользователя."""
    text_lower = text.lower().strip()
    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    
    params = {
        "month": None,
        "country_code": None,
        "country_name": None,
        "adults": 2,
        "nights": 10,
    }
    
    # Поиск месяца
    for word in text_lower.split():
        if word in MONTH_MAPPING:
            params["month"] = MONTH_MAPPING[word]
            break
    
    # Поиск направления
    for dest_name, code in COUNTRY_MAPPING.items():
        if dest_name in text_lower:
            params["country_code"] = code
            params["country_name"] = dest_name
            break
    
    # Поиск количества взрослых
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])
    
    # Поиск количества ночей
    nights_match = re.search(r'(\d+)\s*(?:ночей|ночи|ночь|н\b)', text_lower)
    if nights_match:
        params["nights"] = int(nights_match.group(1))
    
    return params


async def quick_price_scan(
    country_code: str,
    date: str,
    adults: int,
    nights: int
) -> Optional[int]:
    """
    ФАЗА 1: Быстрое сканирование - только минимальная цена на дату.
    Без скролла, без детального парсинга.
    
    ИСПРАВЛЕНИЕ: Ищем туры ±1 ночь от запрошенного количества
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow'
            )
            page = await context.new_page()
            
            try:
                # Ищем с диапазоном ±1 ночь
                nights_min = max(1, nights - 1)
                nights_max = nights + 1
                
                search_url = (
                    f"{LEVELTRAVEL_WEB_URL}/search/"
                    f"Moscow-RU-to-Any-{country_code}-"
                    f"departure-{date}-"
                    f"for-{nights_min}..{nights_max}-nights-"
                    f"{adults}-adults-0-kids-"
                    f"1..5-stars-package-type"
                )
                
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                # Возвращаем к старому таймауту 15 секунд
                try:
                    await page.wait_for_selector('div[class*="DesktopHotelCard_container"]', timeout=15000)
                except Exception:
                    logging.warning(f"Нет результатов для {date}")
                    return None
                
                # Небольшая пауза для загрузки цен
                await page.wait_for_timeout(1000)
                
                # Берем цену первого тура (они отсортированы по рекомендациям, но цена всё равно близка к минимуму)
                min_price = await page.evaluate("""
                    () => {
                        const firstCard = document.querySelector('div[class*="DesktopHotelCard_container"]');
                        if (!firstCard) return null;
                        
                        const priceEl = firstCard.querySelector('div[class*="HotelCardPriceBlock_styledPrice"]');
                        if (!priceEl) return null;
                        
                        const priceText = priceEl.textContent.replace(/\\s/g, '').replace(/&nbsp;/g, '').replace(/\\u00a0/g, '');
                        const priceMatch = priceText.match(/(\\d+)/);
                        return priceMatch ? parseInt(priceMatch[0]) : null;
                    }
                """)
                
                if min_price:
                    logging.info(f"Найдена цена для {date}: {min_price} ₽")
                
                return min_price
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка quick_price_scan для {date}: {e}")
        return None


async def capture_hotel_screenshot(
    hotel_link: str,
    hotel_name: str,
    nights: int
) -> Optional[str]:
    """
    НОВАЯ ФУНКЦИЯ #2: Создает скриншот страницы отеля с расширенной информацией.
    Возвращает путь к сохраненному файлу.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow'
            )
            page = await context.new_page()
            
            try:
                logging.info(f"Создаю скриншот для {hotel_name}")
                
                # Переходим на страницу отеля
                await page.goto(hotel_link, timeout=60000, wait_until='domcontentloaded')
                
                # Ждем загрузки календаря с датами
                try:
                    await page.wait_for_selector('[class*="Calendar"]', timeout=15000)
                except Exception:
                    logging.warning(f"Календарь не загрузился для {hotel_name}")
                
                # Дополнительная пауза для полной загрузки
                await page.wait_for_timeout(3000)
                
                # Прокручиваем к календарю и вариантам номеров
                await page.evaluate("""
                    () => {
                        const calendar = document.querySelector('[class*="Calendar"]');
                        if (calendar) {
                            calendar.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }
                    }
                """)
                
                await page.wait_for_timeout(2000)
                
                # Создаем директорию для скриншотов если её нет
                screenshots_dir = "/tmp/tour_screenshots"
                os.makedirs(screenshots_dir, exist_ok=True)
                
                # Генерируем безопасное имя файла
                safe_name = re.sub(r'[^\w\s-]', '', hotel_name)[:50]
                screenshot_path = f"{screenshots_dir}/{safe_name}_{nights}n.png"
                
                # Делаем скриншот области с календарем и вариантами номеров
                await page.screenshot(
                    path=screenshot_path,
                    full_page=False,
                    type='png'
                )
                
                logging.info(f"Скриншот сохранен: {screenshot_path}")
                return screenshot_path
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка создания скриншота для {hotel_name}: {e}")
        return None


async def deep_parse_date(
    country_code: str,
    date: str,
    adults: int,
    nights: int
) -> List[Dict]:
    """
    ФАЗА 2: Глубокий парсинг - полная информация по дате.
    Со скроллом, рейтингами, локациями.
    
    ИСПРАВЛЕНИЯ: 
    - Ищем туры ±1 ночь от запрошенного
    - Учитываем что сайт сортирует по рекомендациям, а не по цене
    """
    tours = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )
            page = await context.new_page()

            try:
                # Ищем с диапазоном ±1 ночь
                nights_min = max(1, nights - 1)
                nights_max = nights + 1
                
                search_url = (
                    f"{LEVELTRAVEL_WEB_URL}/search/"
                    f"Moscow-RU-to-Any-{country_code}-"
                    f"departure-{date}-"
                    f"for-{nights_min}..{nights_max}-nights-"
                    f"{adults}-adults-0-kids-"
                    f"1..5-stars-package-type"
                )

                logging.info(f"Глубокий парсинг: {date} ({nights_min}-{nights_max} ночей)")
                await page.goto(search_url, timeout=90_000, wait_until="domcontentloaded")

                try:
                    await page.wait_for_selector(
                        'div[class*="DesktopHotelCard_container"]',
                        timeout=40_000,
                    )
                except Exception:
                    logging.warning(f"Карточки не загрузились для {date}")
                    return []

                # Скроллим для подгрузки (сайт сортирует по рекомендациям, нужно больше карточек)
                for _ in range(10):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(1500)

                tours = await page.evaluate(
                    """
                    () => {
                        const results = [];
                        const cards = Array.from(
                            document.querySelectorAll(
                                'div[class*="DesktopHotelCard_container"]'
                            )
                        );

                        for (const card of cards) {
                            try {
                                const tour = {
                                    hotel_name: "Без названия",
                                    price: 0,
                                    rating: 0,
                                    stars: 0,
                                    location: "",
                                    link: "",
                                    nights: 0
                                };

                                const titleEl = card.querySelector(
                                    'a[class*="HotelCardTitle_title"]'
                                );
                                if (titleEl) {
                                    tour.hotel_name = titleEl.textContent.trim();
                                    tour.link = titleEl.getAttribute("href");
                                    if (tour.link && !tour.link.startsWith("http")) {
                                        tour.link = "https://level.travel" + tour.link;
                                    }
                                }

                                const priceEl = card.querySelector(
                                    'div[class*="HotelCardPriceBlock_styledPrice"]'
                                );
                                if (priceEl) {
                                    const text = priceEl.textContent
                                        .replace(/\\s/g, "")
                                        .replace(/\\u00a0/g, "");
                                    const m = text.match(/(\\d+)/);
                                    if (m) tour.price = parseInt(m[1], 10);
                                }

                                const locEl = card.querySelector(
                                    'p[class*="HotelCardLocation_text"]'
                                );
                                if (locEl) tour.location = locEl.textContent.trim();

                                const ratingEl = card.querySelector(
                                    'span[class*="HotelRating_rating"]'
                                );
                                if (ratingEl) {
                                    tour.rating = parseFloat(
                                        ratingEl.textContent.trim()
                                    );
                                }

                                const starsEl = card.querySelector(
                                    'div[class*="HotelStars_container"]'
                                );
                                if (starsEl) {
                                    tour.stars = starsEl.querySelectorAll("svg").length;
                                }
                                
                                // Пытаемся извлечь количество ночей из ссылки
                                if (tour.link) {
                                    const nightsMatch = tour.link.match(/for-(\\d+)-nights/);
                                    if (nightsMatch) {
                                        tour.nights = parseInt(nightsMatch[1], 10);
                                    }
                                }

                                if (tour.price > 1000 && tour.hotel_name !== "Без названия") {
                                    results.push(tour);
                                }
                            } catch (e) {}
                        }

                        return results;
                    }
                    """
                )

            finally:
                await context.close()
                await browser.close()

    except Exception as e:
        logging.error(f"Ошибка deep_parse_date для {date}: {e}")

    return tours


def nights_match(tour_nights: int, target: int) -> bool:
    """Проверяет, подходит ли количество ночей (с погрешностью ±1)."""
    return target - 1 <= tour_nights <= target + 1


async def two_phase_search(
    country_code: str,
    month: Optional[int],
    adults: int,
    nights: int
) -> Dict[str, any]:
    """
    Двухфазный поиск по всему месяцу:
    ФАЗА 1: Быстрое сканирование всех дат → находим самые дешевые
    ФАЗА 2: Глубокий парсинг только перспективных дат
    
    ИСПРАВЛЕНИЕ #3: Корректный расчет статистики
    """
    
    # Генерируем все даты месяца
    all_dates = generate_full_month_dates(month)
    
    logging.info(f"ФАЗА 1: Сканирование {len(all_dates)} дат месяца...")
    
    # ФАЗА 1: Быстрое сканирование всех дат
    date_prices = {}
    for i, date in enumerate(all_dates, 1):
        logging.info(f"Сканирую {i}/{len(all_dates)}: {date}")
        price = await quick_price_scan(country_code, date, adults, nights)
        if price:
            date_prices[date] = price
        # Небольшая задержка между запросами
        await asyncio.sleep(1)
    
    if not date_prices:
        logging.warning("ФАЗА 1: Не найдено ни одной цены")
        return {"hotels": {}, "date_stats": {}}
    
    # Сортируем даты по цене и берем топ-7 самых дешевых
    sorted_dates = sorted(date_prices.items(), key=lambda x: x[1])
    best_dates = [date for date, price in sorted_dates[:7]]
    
    logging.info(f"ФАЗА 1 завершена. Лучшие даты: {best_dates}")
    logging.info(f"ФАЗА 2: Глубокий парсинг {len(best_dates)} перспективных дат...")
    
    # ФАЗА 2: Глубокий парсинг перспективных дат
    hotels = {}  # {hotel_name: best_offer}
    all_parsed_tours = []  # ИСПРАВЛЕНИЕ #3: Собираем ВСЕ спарсенные туры для корректной статистики
    
    for date in best_dates:
        tours = await deep_parse_date(country_code, date, adults, nights)
        
        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower().strip()
            if not hotel_key:
                continue
            
            # НОВОЕ: Фильтруем по количеству ночей (±1)
            tour_nights = tour.get('nights', 0)
            if tour_nights > 0 and not nights_match(tour_nights, nights):
                continue
            
            tour['date'] = date
            # Используем реальное количество ночей из тура, если оно есть
            if tour_nights == 0:
                tour['nights'] = nights
            
            # ИСПРАВЛЕНИЕ #3: Добавляем тур в общий список
            all_parsed_tours.append(tour)
            
            # Сохраняем только ЛУЧШЕЕ предложение для каждого отеля
            if hotel_key not in hotels:
                hotels[hotel_key] = tour
            else:
                # Если нашли дешевле - заменяем
                if tour['price'] < hotels[hotel_key]['price']:
                    hotels[hotel_key] = tour
        
        await asyncio.sleep(2)
    
    # ИСПРАВЛЕНИЕ #3: Корректная статистика
    # Используем цены из ФАЗЫ 1 для общей статистики месяца
    prices_phase1 = list(date_prices.values())
    sorted_prices_phase1 = sorted(prices_phase1)
    n1 = len(sorted_prices_phase1)
    
    # А для детального анализа - цены из спарсенных туров ФАЗЫ 2
    prices_phase2 = [t['price'] for t in all_parsed_tours if t.get('price', 0) > 0]
    sorted_prices_phase2 = sorted(prices_phase2) if prices_phase2 else []
    
    # Медиана для ФАЗЫ 1 (весь месяц)
    median_phase1 = sorted_prices_phase1[n1 // 2] if n1 > 0 else 0

    date_stats = {
        "all_dates_count": len(all_dates),
        "searched_dates": n1,
        # Статистика по ФАЗЕ 1 (весь месяц, быстрое сканирование)
        "min_price": min(prices_phase1) if prices_phase1 else 0,
        "max_price": max(prices_phase1) if prices_phase1 else 0,
        "median_price": median_phase1,
        "price_by_date": date_prices,
        # Статистика по ФАЗЕ 2 (детально спарсенные туры)
        "detailed_min_price": min(prices_phase2) if prices_phase2 else 0,
        "detailed_max_price": max(prices_phase2) if prices_phase2 else 0,
        "detailed_tours_count": len(all_parsed_tours)
    }
    
    logging.info(f"ФАЗА 2 завершена. Уникальных отелей: {len(hotels)}")
    logging.info(f"Статистика: min={date_stats['min_price']}, median={date_stats['median_price']}, max={date_stats['max_price']}")
    logging.info(f"Детальная статистика: min={date_stats['detailed_min_price']}, туров={date_stats['detailed_tours_count']}")
    
    return {
        "hotels": hotels,
        "date_stats": date_stats
    }


async def analyze_tours_with_ai(
    hotels: Dict[str, Dict],
    date_stats: Dict,
    params: Dict
) -> List[Dict]:
    """
    Глубокий AI-анализ с контекстом рынка и развернутыми комментариями.
    """
    if not hotels:
        return []
    
    # Преобразуем словарь в список и сортируем по цене
    tours_list = list(hotels.values())
    tours_list.sort(key=lambda x: x.get("price", 0))
    
    # Берем топ-30 для анализа
    candidates = tours_list[:30]
    
    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    season_info = "Неизвестный сезон"
    if params.get("month"):
        best_months = destination_meta.get("best_months", [])
        season_info = "✅ Отличный сезон" if params["month"] in best_months else "⚠️ Межсезонье/возможны дожди"
    
    # Рассчитываем рыночный контекст вручную (без модуля statistics)
    prices = [t['price'] for t in candidates]
    ratings = [t['rating'] for t in candidates if t.get('rating', 0) > 0]
    
    # Считаем среднее и медиану простыми методами
    avg_price = int(sum(prices) / len(prices)) if prices else 0
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
    sorted_prices = sorted(prices)
    median_price = sorted_prices[len(sorted_prices) // 2] if sorted_prices else 0

    market_context = {
        "min_price": min(prices) if prices else 0,
        "max_price": max(prices) if prices else 0,
        "avg_price": avg_price,
        "median_price": int(median_price),
        "avg_rating": avg_rating,
        "month_min_price": date_stats.get("min_price", 0),
        "month_max_price": date_stats.get("max_price", 0),
        "month_median_price": int(date_stats.get("median_price", 0))
    }
    
    # Добавляем к каждому туру метрики относительно рынка
    for tour in candidates:
        price = tour['price']
        rating = tour.get('rating', 0)
        
        tour['price_vs_min'] = round((price / market_context['min_price'] - 1) * 100, 1) if market_context['min_price'] else 0
        tour['price_vs_median'] = round((price / market_context['median_price'] - 1) * 100, 1) if market_context['median_price'] else 0
        tour['rating_vs_avg'] = round(rating - market_context['avg_rating'], 1) if rating > 0 else None
    
    prompt = f"""
Ты - профессиональный турагент-аналитик. Проведи глубокий анализ рынка туров в {params.get('country_name', 'направление').title()} и выбери ТОП-7 предложений.

КОНТЕКСТ НАПРАВЛЕНИЯ:
• Описание: {destination_meta.get('description', '')}
• Сезонность: {season_info}
• Взрослых: {params['adults']}
• Ночей: {params['nights']}

РЫНОЧНАЯ СТАТИСТИКА:
• Минимальная цена месяца: {market_context['month_min_price']:,} ₽
• Максимальная цена месяца: {market_context['month_max_price']:,} ₽
• Медиана месяца: {market_context['month_median_price']:,} ₽
• Средняя цена в выборке: {market_context['avg_price']:,} ₽
• Средний рейтинг: {market_context['avg_rating']}

КАНДИДАТЫ (топ-30 отелей с лучшими ценами):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

ЗАДАЧА:
Выбери ТОП-7 предложений по разным сценариям:
1. Минимальный бюджет (но не хлам)
2. Лучший баланс цена/качество
3. Премиум с отличным рейтингом
4. Удачная дата (например, начало недели дешевле)
5-7. Дополнительные интересные варианты

ДОПОЛНИТЕЛЬНАЯ ЗАДАЧА (КРИТИЧЕСКИ ВАЖНО):

Для КАЖДОГО выбранного отеля:
1. Используй рейтинг Level.Travel как "внутренний рейтинг агрегатора".
2. Самостоятельно оцени репутацию отеля, опираясь на:
   • Booking.com
   • Google Maps
   • Agoda
   • TripAdvisor
   • Expedia
   • Airbnb
   (если информации нет — честно укажи это)

3. В комментарии ЯВНО напиши:
   • примерный средний рейтинг по внешним источникам
   • ориентировочное количество отзывов (мало / средне / много)
   • совпадает ли оценка с Level.Travel или есть расхождение

4. Если рейтинг Level.Travel сильно выше, чем внешние отзывы:
   • обязательно отметь это как риск
   • объясни возможную причину (мало отзывов, новый отель, бутик)

5. Все цены, если упоминаешь, указывай в РУБЛЯХ.

КРИТЕРИИ:
• НЕ выбирай отели с рейтингом < 7.0, если есть альтернативы
• Учитывай отклонение цены от медианы (price_vs_median)
• Разнообразь выбор по звездности и локации
• Обрати внимание на даты (будни vs выходные)

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON массив из 7 объектов:
[
  {{
    "index": 0,
    "ai_score": 9,
    "scenario": "Минимальный бюджет",
    "reason": "Островной бутик-отель на Ченингане 🌴 Рейтинг Level.Travel — 10.0, но по внешним источникам средняя оценка ~4.6/5 (Booking, Google), около 300 отзывов. Хвалят виды и сервис, из минусов — сложный подъезд и приливы. Цена ~151 000 ₽ выглядит оправданной."
  }},
  ...
]

Поля:
• index - номер в массиве candidates (0-29)
• ai_score - оценка 1-10
• scenario - сценарий использования (1-2 слова)
• reason - развернутый комментарий (15-30 слов), почему выбрал, какие преимущества, используй эмодзи

ВАЖНО: reason должен быть информативным, не просто "хорошо", а конкретные факты и цифры!
"""

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)
            
            # Ищем JSON в ответе
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                ai_results = json.loads(json_match.group(0))
                
                final_tours = []
                for item in ai_results:
                    idx = item.get('index')
                    if idx is not None and isinstance(idx, int) and 0 <= idx < len(candidates):
                        tour = candidates[idx].copy()
                        tour['ai_score'] = item.get('ai_score', 0)
                        tour['scenario'] = item.get('scenario', 'Выбор AI')
                        tour['ai_reason'] = item.get('reason', 'Рекомендация AI')
                        final_tours.append(tour)
                
                final_tours.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
                
                if final_tours:
                    logging.info(f"AI вернул {len(final_tours)} рекомендаций")
                    return final_tours

    except Exception as e:
        logging.error(f"Ошибка AI анализа: {e}")
    
    # Фолбек: умная сортировка без AI
    logging.info("Использую фолбек (без AI)")
    
    # Фильтруем плохие отели
    good_tours = [t for t in candidates if t.get('rating', 0) >= 6.0]
    if not good_tours:
        good_tours = candidates
    
    # Сортируем по соотношению цена/рейтинг
    for tour in good_tours:
        rating = tour.get('rating', 5.0)
        if rating > 0:
            tour['value_score'] = rating / (tour['price'] / 10000)
        else:
            tour['value_score'] = 0
    
    good_tours.sort(key=lambda x: x.get('value_score', 0), reverse=True)
    
    return good_tours[:7]


def format_tours_message(
    tours: List[Dict],
    params: Dict,
    date_stats: Dict
) -> str:
    """Форматирует список туров с расширенной информацией."""
    if not tours:
        return "😢 Туры не найдены"

    country_name = params.get("country_name", "направление").capitalize()

    header = (
        f"🏖 <b>Топ подборка: {country_name}</b>\n"
        f"👥 {params['adults']} взр. | 🌙 {params['nights']} ночей\n\n"
    )

    if date_stats:
        header += (
            f"📊 <b>Анализ месяца:</b>\n"
            f"• Проверено дат: {date_stats.get('searched_dates', 0)}\n"
            f"• Минимум: {date_stats.get('min_price', 0):,} ₽\n"
            f"• Медиана: {int(date_stats.get('median_price', 0)):,} ₽\n"
            f"• Максимум: {date_stats.get('max_price', 0):,} ₽\n"
        )

    lines = [header]

    for i, tour in enumerate(tours, 1):
        link = tour.get("link", "#")
        name = tour.get("hotel_name", "Отель")

        lines.append(f"\n<b>{i}. <a href='{link}'>{name}</a></b>")

        if tour.get("scenario"):
            lines.append(f"🎯 <i>{tour['scenario']}</i>")

        # ===== ДИАПАЗОН ДАТ =====
        start_date_str = tour.get("date", "")
        nights = tour.get("nights", params.get("nights", 0))

        date_range = ""
        try:
            start_dt = datetime.strptime(start_date_str, "%d.%m.%Y")
            end_dt = start_dt + timedelta(days=nights)
            date_range = (
                f"📅 {start_dt.strftime('%d.%m.%Y')}-"
                f"{end_dt.strftime('%d.%m.%Y')}"
            )
        except Exception:
            if start_date_str:
                date_range = f"📅 {start_date_str}"

        stars = "⭐️" * tour.get("stars", 0)
        meta = " | ".join(p for p in [stars, date_range] if p)
        if meta:
            lines.append(meta)

        # ===== РЕЙТИНГ LEVEL.TRAVEL =====
        rating = tour.get("rating", 0)
        if rating > 0:
            lines.append(f"📊 Рейтинг Level.Travel: {rating}")

        if tour.get("location"):
            lines.append(f"📍 {tour['location']}")

        if tour.get("ai_reason"):
            lines.append(f"🤖 <i>{tour['ai_reason']}</i>")

        price = tour.get("price", 0)
        price_line = f"💰 <b>{price:,} ₽</b>"

        diff = tour.get("price_vs_median")
        if diff is not None:
            if diff < -10:
                price_line += " 🔥 Выгодно!"
            elif diff < -5:
                price_line += " ✅"

        lines.append(price_line)

    return "\n".join(lines)


async def process_tours_command(message: types.Message):
    """
    Главный обработчик команды поиска туров.
    
    НОВОЕ #2: Теперь отправляет результаты по одному сообщению на тур со скриншотом
    """
    # Проверка доступа
    if ADMIN_ID and message.from_user.id != int(ADMIN_ID):
        await message.reply("🚫 Доступ к поиску туров только для администратора.")
        return
    
    try:
        params = parse_tour_command(message.text)
        
        if not params.get("country_code"):
            await message.reply(
                "❌ Не понял направление. Укажите страну и месяц.\n"
                "Пример: <i>туры апрель шри-ланка 2</i>",
                parse_mode="HTML"
            )
            return
        
        status_msg = await message.reply(
            f"🔍 <b>Запускаю поиск туров</b>\n\n"
            f"📍 Направление: {params.get('country_name', '').title()}\n"
            f"📅 Месяц: весь {list(MONTH_MAPPING.keys())[params.get('month', 1) * 2 - 2] if params.get('month') else 'не указан'}\n"
            f"👥 Взрослых: {params['adults']}\n"
            f"🌙 Ночей: {params['nights']} (ищем {params['nights']-1}-{params['nights']+1})\n\n"
            f"⏳ <b>ФАЗА 1:</b> Быстрое сканирование всех дат месяца...\n"
            f"Это займет 3-5 минут.",
            parse_mode="HTML"
        )
        
        # Двухфазный поиск
        result = await two_phase_search(
            country_code=params["country_code"],
            month=params.get("month"),
            adults=params["adults"],
            nights=params["nights"]
        )
        
        hotels = result["hotels"]
        date_stats = result["date_stats"]
        
        if not hotels:
            await status_msg.edit_text(
                "😕 Ничего не нашел.\n"
                "Возможно, нет доступных туров на этот период\n"
                "или проблемы с сайтом.\n"
                "Попробуйте другой месяц или направление."
            )
            return
        
        # Сообщение о начале ФАЗЫ 2
        await status_msg.edit_text(
            f"✅ <b>ФАЗА 1 завершена!</b>\n"
            f"Проверено дат: {date_stats.get('searched_dates', 0)}\n"
            f"Найдено предложений: {date_stats.get('detailed_tours_count', 0)}\n"
            f"Уникальных отелей: {len(hotels)}\n\n"
            f"⏳ <b>ФАЗА 2:</b> Запускаю AI-анализ для выбора лучших...\n"
            f"Это займет 10-15 секунд.",
            parse_mode="HTML"
        )
        
        # AI анализ
        best_tours = await analyze_tours_with_ai(hotels, date_stats, params)
        
        await status_msg.edit_text(
            f"✅ <b>Анализ завершен!</b>\n"
            f"Отобрано {len(best_tours)} лучших предложений\n\n"
            f"⏳ Создаю скриншоты...\n"
            f"Это займет 30-60 секунд.",
            parse_mode="HTML"
        )
        
        # НОВОЕ #2: Отправляем результаты по одному с скриншотами
        country_name = params.get("country_name", "направление").capitalize()
        
        # Формируем заголовок
        header = (
            f"🏖 <b>Топ подборка: {country_name}</b>\n"
            f"👥 {params['adults']} взр. | 🌙 {params['nights']} ночей\n\n"
        )
        
        if date_stats:
            header += (
                f"📊 <b>Анализ месяца:</b>\n"
                f"• Проверено дат: {date_stats.get('searched_dates', 0)}\n"
                f"• Минимум: {date_stats.get('min_price', 0):,} ₽\n"
                f"• Медиана: {int(date_stats.get('median_price', 0)):,} ₽\n"
                f"• Максимум: {date_stats.get('max_price', 0):,} ₽\n\n"
                f"📸 Скриншоты показывают:\n"
                f"• Календарь с ценами на разные даты\n"
                f"• Варианты номеров с ценами\n"
                f"• Информацию о завтраках\n"
            )
        
        # Удаляем статусное сообщение
        await status_msg.delete()
        
        # Отправляем заголовок
        await message.reply(header, parse_mode="HTML")
        
        # Отправляем каждый тур отдельным сообщением со скриншотом
        for i, tour in enumerate(best_tours, 1):
            try:
                # Формируем текст для одного тура
                link = tour.get("link", "#")
                name = tour.get("hotel_name", "Отель")
                
                tour_text = f"<b>{i}. <a href='{link}'>{name}</a></b>\n"
                
                if tour.get("scenario"):
                    tour_text += f"🎯 <i>{tour['scenario']}</i>\n"
                
                # Диапазон дат
                start_date_str = tour.get("date", "")
                nights = tour.get("nights", params.get("nights", 0))
                
                try:
                    start_dt = datetime.strptime(start_date_str, "%d.%m.%Y")
                    end_dt = start_dt + timedelta(days=nights)
                    date_range = f"📅 {start_dt.strftime('%d.%m.%Y')}-{end_dt.strftime('%d.%m.%Y')}"
                except Exception:
                    date_range = f"📅 {start_date_str}" if start_date_str else ""
                
                stars = "⭐️" * tour.get("stars", 0)
                meta = " | ".join(p for p in [stars, date_range] if p)
                if meta:
                    tour_text += meta + "\n"
                
                rating = tour.get("rating", 0)
                if rating > 0:
                    tour_text += f"📊 Рейтинг Level.Travel: {rating}\n"
                
                if tour.get("location"):
                    tour_text += f"📍 {tour['location']}\n"
                
                if tour.get("ai_reason"):
                    tour_text += f"🤖 <i>{tour['ai_reason']}</i>\n"
                
                price = tour.get("price", 0)
                price_line = f"💰 <b>{price:,} ₽</b>"
                
                diff = tour.get("price_vs_median")
                if diff is not None:
                    if diff < -10:
                        price_line += " 🔥 Выгодно!"
                    elif diff < -5:
                        price_line += " ✅"
                
                tour_text += price_line
                
                # Создаем скриншот
                screenshot_path = None
                if link and link != "#":
                    screenshot_path = await capture_hotel_screenshot(
                        link, 
                        name, 
                        nights
                    )
                
                # Отправляем сообщение со скриншотом или без
                if screenshot_path and os.path.exists(screenshot_path):
                    try:
                        # Используем FSInputFile для правильной отправки фото
                        photo = FSInputFile(screenshot_path)
                        await message.reply_photo(
                            photo=photo,
                            caption=tour_text,
                            parse_mode="HTML"
                        )
                        # Удаляем временный файл
                        os.remove(screenshot_path)
                    except Exception as e:
                        logging.error(f"Ошибка отправки скриншота: {e}")
                        await message.reply(tour_text, parse_mode="HTML", disable_web_page_preview=True)
                else:
                    await message.reply(tour_text, parse_mode="HTML", disable_web_page_preview=True)
                
                # Небольшая пауза между сообщениями
                await asyncio.sleep(1)
                
            except Exception as e:
                logging.error(f"Ошибка отправки тура #{i}: {e}")
                continue
        
        logging.info(f"Отправлено {len(best_tours)} туров пользователю {message.from_user.id}")
        
    except Exception as e:
        logging.error(f"Ошибка в process_tours_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(e)}")


# =============================================================================
# ТЕСТ
# =============================================================================

if __name__ == "__main__":
    async def test():
        print("Запуск теста двухфазного поиска...")
        result = await two_phase_search("ID", 5, 2, 7)
        print(f"\nНайдено отелей: {len(result['hotels'])}")
        print(f"Статистика дат: {result['date_stats']}")
        
        tours = list(result['hotels'].values())[:5]
        for t in tours:
            print(f"\n{t['hotel_name']} - {t['price']:,} ₽ ({t['date']})")
            
    asyncio.run(test())
