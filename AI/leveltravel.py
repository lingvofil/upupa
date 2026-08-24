import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from playwright.async_api import async_playwright
from aiogram import types
from aiogram.types import FSInputFile, InputMediaPhoto
import json
import os

from core.settings import ADMIN_ID
from infrastructure.ai.clients import groq_ai

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

LEVELTRAVEL_WEB_URL = "https://level.travel"

# Типы поиска
SEARCH_TYPE_TOUR = "tour"  # Тур с перелетом
SEARCH_TYPE_HOTEL = "hotel"  # Только отель (без перелета)

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
DESTINATION_MAPPING = {
    "северный гоа": {"country_code": "IN", "location_slug": None},
    "гоа": {"country_code": "IN", "location_slug": None},
    "мальдивы": {"country_code": "MV", "location_slug": None},
    "шри-ланка": {"country_code": "LK", "location_slug": None},
    "шриланка": {"country_code": "LK", "location_slug": None},
    "вьетнам": {"country_code": "VN", "location_slug": None},
    "фукуок": {"country_code": "VN", "location_slug": "Phu.Quoc-VN"},
    "нячанг": {"country_code": "VN", "location_slug": "Nha.Trang-VN"},
    "турция": {"country_code": "TR", "location_slug": None},
    "бали": {"country_code": "ID", "location_slug": None},
    "индонезия": {"country_code": "ID", "location_slug": None},
    "таиланд": {"country_code": "TH", "location_slug": None},
    "пхукет": {"country_code": "TH", "location_slug": None},
    "паттайя": {"country_code": "TH", "location_slug": None},
    "оаэ": {"country_code": "AE", "location_slug": None},
    "дубай": {"country_code": "AE", "location_slug": None},
    "египет": {"country_code": "EG", "location_slug": None},
    "хургада": {"country_code": "EG", "location_slug": None},
    "шарм": {"country_code": "EG", "location_slug": None},
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


def parse_date_range(text: str) -> Optional[Tuple[str, str]]:
    """
    Парсит диапазон дат из строки.
    Поддерживаемые форматы:
    - 18.05.26-25.05.26
    - 18.05.2026-25.05.2026
    - 18.05-25.05
    
    Returns: (start_date, end_date) в формате DD.MM.YYYY или None
    """
    # Паттерн для полной даты с годом
    pattern_full = r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})'
    # Паттерн для даты без года (используем текущий/следующий год)
    pattern_short = r'(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})'
    
    match_full = re.search(pattern_full, text)
    if match_full:
        d1, m1, y1, d2, m2, y2 = match_full.groups()
        # Если год двузначный, добавляем 2000
        y1 = int(y1) if len(y1) == 4 else 2000 + int(y1)
        y2 = int(y2) if len(y2) == 4 else 2000 + int(y2)
        
        try:
            start = datetime(y1, int(m1), int(d1))
            end = datetime(y2, int(m2), int(d2))
            return (start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y"))
        except ValueError:
            return None
    
    match_short = re.search(pattern_short, text)
    if match_short:
        d1, m1, d2, m2 = match_short.groups()
        current_year = datetime.now().year
        
        try:
            start = datetime(current_year, int(m1), int(d1))
            end = datetime(current_year, int(m2), int(d2))
            
            # Если даты в прошлом, берем следующий год
            if start < datetime.now():
                start = start.replace(year=current_year + 1)
                end = end.replace(year=current_year + 1)
            
            return (start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y"))
        except ValueError:
            return None
    
    return None


def calculate_nights(start_date: str, end_date: str) -> int:
    """Вычисляет количество ночей между датами."""
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
        return (end - start).days
    except Exception:
        return 7  # Дефолт


def parse_search_command(text: str, search_type: str = SEARCH_TYPE_TOUR) -> Dict:
    """
    Парсит команду поиска ("туры" или "отели").
    
    НОВОЕ: 
    - Поддержка точных дат (18.05.26-25.05.26)
    - Поддержка множественных направлений (фукуок гоа мальдивы)
    - Разделение логики для туров и отелей
    
    Args:
        text: текст команды
        search_type: "tour" или "hotel"
    
    Возвращает:
    {
        "month": int или None,
        "countries": [{"code": "IN", "name": "гоа"}, ...],
        "adults": int,
        "nights": int,
        "exact_dates": {"start": "18.05.2026", "end": "25.05.2026"} или None,
        "search_type": "tour" или "hotel"
    }
    """
    text_lower = text.lower().strip()
    
    # Убираем префикс команды
    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    elif text_lower.startswith("отели"):
        text_lower = text_lower[5:].strip()
    
    params = {
        "month": None,
        "countries": [],
        "adults": 2,
        "nights": 10,
        "exact_dates": None,
        "search_type": search_type
    }
    
    # 1. Проверяем наличие точных дат
    date_range = parse_date_range(text_lower)
    if date_range:
        params["exact_dates"] = {"start": date_range[0], "end": date_range[1]}
        params["nights"] = calculate_nights(date_range[0], date_range[1])
        logging.info(f"Найдены точные даты: {date_range[0]} - {date_range[1]} ({params['nights']} ночей)")
    
    # 2. Ищем ночи (если не заданы через даты)
    nights_match = re.search(r'(\d+)\s*(?:ночей|ночи|ночь|н\b)', text_lower)
    if nights_match and not params["exact_dates"]:
        params["nights"] = int(nights_match.group(1))
        text_lower = text_lower.replace(nights_match.group(0), "")
    
    # 3. Поиск месяца (если нет точных дат)
    if not params["exact_dates"]:
        for word in text_lower.split():
            if word in MONTH_MAPPING:
                params["month"] = MONTH_MAPPING[word]
                break
    
    # 4. Поиск ВСЕХ направлений в тексте
    for dest_name in sorted(DESTINATION_MAPPING.keys(), key=len, reverse=True):
        if dest_name in text_lower:
            dest_meta = DESTINATION_MAPPING[dest_name]
            code = dest_meta["country_code"]
            location_slug = dest_meta.get("location_slug")

            if location_slug is None and any(
                c["code"] == code and c.get("location_slug") for c in params["countries"]
            ):
                continue

            if not any(
                c["code"] == code and c.get("location_slug") == location_slug
                for c in params["countries"]
            ):
                params["countries"].append({
                    "code": code,
                    "name": dest_name,
                    "location_slug": location_slug
                })
    
    # 5. Поиск взрослых
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])
    
    return params


def build_search_url(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None
) -> str:
    """
    Строит URL для поиска туров или отелей.
    
    Args:
        country_code: код страны (например, "VN")
        date: дата вылета/заезда в формате DD.MM.YYYY
        adults: количество взрослых
        nights: количество ночей
        search_type: "tour" (с перелетом) или "hotel" (только отель)
    
    Returns:
        Полный URL для поиска
    """
    nights_min = max(1, nights - 1)
    nights_max = nights + 1
    
    destination_part = destination_slug or f"Any-{country_code}"

    if search_type == SEARCH_TYPE_HOTEL:
        # URL для поиска отелей (без перелета)
        # Пример: https://level.travel/search/Any-RU-to-Phu.Quoc-VN-departure-from-28.04.2026..02.05.2026-to-06.05.2026..10.05.2026-2-adults-0-kids-1..5-stars-hotel-type-30.04.2026-08.05.2026
        try:
            start_date = datetime.strptime(date, "%d.%m.%Y")
            end_date = start_date + timedelta(days=nights)
            
            # Диапазоны дат (flex ±2 дня)
            start_min = (start_date - timedelta(days=2)).strftime("%d.%m.%Y")
            start_max = (start_date + timedelta(days=2)).strftime("%d.%m.%Y")
            end_min = (end_date - timedelta(days=2)).strftime("%d.%m.%Y")
            end_max = (end_date + timedelta(days=2)).strftime("%d.%m.%Y")
            
            return (
                f"{LEVELTRAVEL_WEB_URL}/search/"
                f"Any-RU-to-{destination_part}-"
                f"departure-from-{start_min}..{start_max}-"
                f"to-{end_min}..{end_max}-"
                f"{adults}-adults-0-kids-"
                f"1..5-stars-hotel-type-"
                f"{date}-{end_date.strftime('%d.%m.%Y')}"
            )
        except Exception as e:
            logging.error(f"Ошибка построения URL для отеля: {e}")
            # Фолбек на обычный URL туров
            return build_search_url(country_code, date, adults, nights, SEARCH_TYPE_TOUR)
    else:
        # URL для туров (с перелетом) - оригинальная логика
        return (
            f"{LEVELTRAVEL_WEB_URL}/search/"
            f"Moscow-RU-to-{destination_part}-"
            f"departure-{date}-"
            f"for-{nights_min}..{nights_max}-nights-"
            f"{adults}-adults-0-kids-"
            f"1..5-stars-package-type"
        )


def generate_full_month_dates(month: Optional[int] = None) -> List[str]:
    """Генерирует ВСЕ возможные даты вылета для месяца."""
    dates = []
    today = datetime.now()
    
    if month:
        year = today.year if month >= today.month else today.year + 1
        day = 1
        while True:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%d.%m.%Y"))
                day += 1
            except ValueError:
                break
    else:
        for i in range(1, 31):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%d.%m.%Y"))
    
    return dates


def generate_date_range_list(start_date: str, end_date: str) -> List[str]:
    """
    Генерирует список всех дат вылета в указанном диапазоне.
    Например, для 18.05.26-25.05.26 вернет: [18.05.26, 19.05.26, ..., 25.05.26]
    """
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
        
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%d.%m.%Y"))
            current += timedelta(days=1)
        
        return dates
    except Exception as e:
        logging.error(f"Ошибка генерации диапазона дат: {e}")
        return [start_date]  # Фолбек


async def quick_price_scan(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None
) -> Optional[int]:
    """ФАЗА 1: Быстрое сканирование - только минимальная цена на дату."""
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
                search_url = build_search_url(
                    country_code,
                    date,
                    adults,
                    nights,
                    search_type,
                    destination_slug=destination_slug
                )
                
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                try:
                    await page.wait_for_selector('div[class*="DesktopHotelCard_container"]', timeout=15000)
                except Exception:
                    logging.warning(f"Нет результатов для {date}")
                    return None
                
                await page.wait_for_timeout(1000)
                
                # ВАЖНО: Разные селекторы для туров и отелей
                if search_type == SEARCH_TYPE_HOTEL:
                    price_selector = 'div[class*="HotelCardPriceBlock_styledHotelCardPrice"]'
                else:
                    price_selector = 'div[class*="HotelCardPriceBlock_styledPrice"]'
                
                min_price = await page.evaluate(f"""
                    () => {{
                        const firstCard = document.querySelector('div[class*="DesktopHotelCard_container"]');
                        if (!firstCard) return null;
                        
                        const priceEl = firstCard.querySelector('{price_selector}');
                        if (!priceEl) return null;
                        
                        const priceText = priceEl.textContent.replace(/\\s/g, '').replace(/&nbsp;/g, '').replace(/\\u00a0/g, '');
                        const priceMatch = priceText.match(/(\\d+)/);
                        return priceMatch ? parseInt(priceMatch[0]) : null;
                    }}
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


async def capture_hotel_screenshots(
    hotel_link: str,
    hotel_name: str,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR
) -> List[str]:
    """Создает ДВА скриншота: календарь и варианты номеров."""
    paths = []
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
                logging.info(f"Создаю скриншоты для {hotel_name} (тип: {search_type})")
                await page.goto(hotel_link, timeout=60000, wait_until='domcontentloaded')
                
                # Скрываем все лишнее
                await page.evaluate("""
                    () => {
                        const selectors = [
                            '[class*="CookieConsent"]', 
                            '[class*="WidgetContainer"]', 
                            '#jivo-iframe-container',
                            '[class*="StickyButton"]',
                            '[class*="HeaderWrapper"]',
                            '[class*="StickyFilter"]',
                            '[class*="StickyPrice"]',
                            '[class*="Floating"]'
                        ];
                        selectors.forEach(s => {
                            const el = document.querySelector(s);
                            if (el) el.style.display = 'none';
                        });
                    }
                """)

                try:
                    await page.wait_for_selector(
                        '[class*="Calendar"], [class*="PriceGrid"], [class*="HotelHeader"], .hotel-content', 
                        timeout=20000
                    )
                except Exception:
                    logging.warning(f"Контент для {hotel_name} не найден по селекторам")
                
                await page.wait_for_timeout(2000)
                
                # СКРИНШОТ 1: Календарь / общий вид
                await page.evaluate("""
                    () => {
                        const target = document.querySelector('[class*="Calendar"]') || 
                                       document.querySelector('[class*="PriceGrid"]') ||
                                       document.querySelector('[class*="HotelHeader"]');
                        if (target) {
                            target.scrollIntoView({ behavior: 'auto', block: 'center' });
                        }
                    }
                """)
                await page.wait_for_timeout(1000)

                screenshots_dir = "/tmp/tour_screenshots"
                os.makedirs(screenshots_dir, exist_ok=True)
                safe_name = re.sub(r'[^\w\s-]', '', hotel_name)[:50]
                
                path1 = f"{screenshots_dir}/{safe_name}_1_calendar.png"
                await page.screenshot(path=path1, full_page=False, type='png')
                paths.append(path1)
                path2 = f"{screenshots_dir}/{safe_name}_2_rooms.png"

                # --- НАЧАЛО БЛОКА ВТОРОГО СКРИНШОТА (FIXED) ---

                # 1. Увеличиваем высоту
                await page.set_viewport_size({'width': 1920, 'height': 2000})

                # 2. Ждем исчезновения скелетонов/загрузчиков (КРИТИЧЕСКИ ВАЖНО)
                # Это предотвращает ошибку "Element is not attached"
                try:
                    await page.wait_for_selector(
                        '[class*="Skeleton"], [class*="Loader"], [class*="Placeholder"]', 
                        state='detached', 
                        timeout=5000
                    )
                except Exception:
                    pass # Если скелетонов нет, идем дальше

                # 3. Определяем селектор и ждем его появления
                target_selector = 'article[class*="HotelRoomCard_roomCard"]'
                try:
                    # Ждем именно появления элемента в DOM
                    await page.wait_for_selector(target_selector, state='attached', timeout=20000)
                except Exception:
                    # Фолбек, если селектор сменился
                    target_selector = 'div[class*="HotelRoom"]:not([class*="Container"])'

                # 4. Скрываем шапку через CSS (безопаснее, чем remove())
                # display: none !important гарантирует, что шапка исчезнет визуально, но не сломает скрипты сайта
                await page.add_style_tag(content="""
                    header, [class*="Header"], [class*="Sticky"], [class*="Filter"], [class*="Head"], #header {
                        display: none !important;
                        opacity: 0 !important;
                        pointer-events: none !important;
                    }
                """)
                
                # Даем браузеру время применить стили
                await page.wait_for_timeout(500)

                # 5. Получаем свежий хендл элемента
                element = await page.query_selector(target_selector)
                
                if element:
                    # Используем JS Scroll вместо Playwright scroll (он надежнее при динамике)
                    await page.evaluate("el => el.scrollIntoView({block: 'center'})", element)
                    
                    # Ждем подгрузки картинок
                    await page.wait_for_timeout(1500)

                    # Получаем координаты СВЕЖЕГО элемента
                    box = await element.bounding_box()
                    
                    if box:
                        # Делаем скриншот с обрезкой (Clip)
                        # box['y'] - это точная координата верха карточки
                        await page.screenshot(
                            path=path2,
                            full_page=False,
                            clip={
                                'x': 0,
                                'y': box['y'],    # Режем строго по верху карточки
                                'width': 1920,
                                'height': 1500
                            }
                        )
                        paths.append(path2)
                    else:
                        logging.warning("Не удалось получить координаты bounding_box")
                        # Аварийный вариант
                        await page.screenshot(path=path2, full_page=False)
                        paths.append(path2)
                else:
                    logging.warning(f"Элемент {target_selector} не найден после ожидания")

                # Возвращаем viewport
                await page.set_viewport_size({'width': 1920, 'height': 1080})

                # --- КОНЕЦ БЛОКА ---
                
                logging.info(f"Скриншоты созданы: {len(paths)}")
                return paths
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка захвата экрана для {hotel_name}: {e}")
        return paths


async def deep_parse_date(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None
) -> List[Dict]:
    """ФАЗА 2: Глубокий парсинг - полная информация по дате."""
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
                search_url = build_search_url(
                    country_code,
                    date,
                    adults,
                    nights,
                    search_type,
                    destination_slug=destination_slug
                )

                logging.info(f"Глубокий парсинг: {date} ({nights} ночей, тип: {search_type})")
                await page.goto(search_url, timeout=90_000, wait_until="domcontentloaded")

                try:
                    await page.wait_for_selector(
                        'div[class*="DesktopHotelCard_container"]',
                        timeout=40_000,
                    )
                except Exception:
                    logging.warning(f"Карточки не загрузились для {date}")
                    return []

                for _ in range(10):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(1500)

                # ВАЖНО: Разные селекторы цены для туров и отелей
                price_selector = (
                    'div[class*="HotelCardPriceBlock_styledHotelCardPrice"]'
                    if search_type == SEARCH_TYPE_HOTEL
                    else 'div[class*="HotelCardPriceBlock_styledPrice"]'
                )

                tours = await page.evaluate(
                    f"""
                    () => {{
                        const results = [];
                        const cards = Array.from(
                            document.querySelectorAll(
                                'div[class*="DesktopHotelCard_container"]'
                            )
                        );

                        for (const card of cards) {{
                            try {{
                                const tour = {{
                                    hotel_name: "Без названия",
                                    price: 0,
                                    rating: 0,
                                    stars: 0,
                                    location: "",
                                    link: "",
                                    nights: 0
                                }};

                                const titleEl = card.querySelector(
                                    'a[class*="HotelCardTitle_title"]'
                                );
                                if (titleEl) {{
                                    tour.hotel_name = titleEl.textContent.trim();
                                    tour.link = titleEl.getAttribute("href");
                                    if (tour.link && !tour.link.startsWith("http")) {{
                                        tour.link = "https://level.travel" + tour.link;
                                    }}
                                }}

                                const priceEl = card.querySelector(
                                    '{price_selector}'
                                );
                                if (priceEl) {{
                                    const text = priceEl.textContent
                                        .replace(/\\s/g, "")
                                        .replace(/\\u00a0/g, "");
                                    const m = text.match(/(\\d+)/);
                                    if (m) tour.price = parseInt(m[1], 10);
                                }}

                                const locEl = card.querySelector(
                                    'p[class*="HotelCardLocation_text"]'
                                );
                                if (locEl) tour.location = locEl.textContent.trim();

                                const ratingEl = card.querySelector(
                                    'span[class*="HotelRating_rating"]'
                                );
                                if (ratingEl) {{
                                    tour.rating = parseFloat(
                                        ratingEl.textContent.trim()
                                    );
                                }}

                                const starsEl = card.querySelector(
                                    'div[class*="HotelStars_container"]'
                                );
                                if (starsEl) {{
                                    tour.stars = starsEl.querySelectorAll("svg").length;
                                }}
                                
                                if (tour.link) {{
                                    const nightsMatch = tour.link.match(/for-(\\d+)-nights/);
                                    if (nightsMatch) {{
                                        tour.nights = parseInt(nightsMatch[1], 10);
                                    }}
                                }}

                                if (tour.price > 1000 && tour.hotel_name !== "Без названия") {{
                                    results.push(tour);
                                }}
                            }} catch (e) {{}}
                        }}

                        return results;
                    }}
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
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR
) -> Dict[str, any]:
    """
    Двухфазный поиск по всему месяцу:
    ФАЗА 1: Быстрое сканирование всех дат → находим самые дешевые
    ФАЗА 2: Глубокий парсинг только перспективных дат
    """
    
    all_dates = generate_full_month_dates(month)
    
    logging.info(f"ФАЗА 1: Сканирование {len(all_dates)} дат месяца...")
    
    date_prices = {}
    for i, date in enumerate(all_dates, 1):
        logging.info(f"Сканирую {i}/{len(all_dates)}: {date}")
        price = await quick_price_scan(country_code, date, adults, nights, search_type)
        if price:
            date_prices[date] = price
        await asyncio.sleep(1)
    
    if not date_prices:
        logging.warning("ФАЗА 1: Не найдено ни одной цены")
        return {"hotels": {}, "date_stats": {}}
    
    sorted_dates = sorted(date_prices.items(), key=lambda x: x[1])
    best_dates = [date for date, price in sorted_dates[:7]]
    
    logging.info(f"ФАЗА 1 завершена. Лучшие даты: {best_dates}")
    logging.info(f"ФАЗА 2: Глубокий парсинг {len(best_dates)} перспективных дат...")
    
    hotels = {}
    all_parsed_tours = []
    
    for date in best_dates:
        tours = await deep_parse_date(country_code, date, adults, nights, search_type)
        
        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower().strip()
            if not hotel_key:
                continue
            
            tour_nights = tour.get('nights', 0)
            if tour_nights > 0 and not nights_match(tour_nights, nights):
                continue
            
            tour['date'] = date
            if tour_nights == 0:
                tour['nights'] = nights
            
            all_parsed_tours.append(tour)
            
            if hotel_key not in hotels:
                hotels[hotel_key] = tour
            else:
                if tour['price'] < hotels[hotel_key]['price']:
                    hotels[hotel_key] = tour
        
        await asyncio.sleep(2)
    
    prices_phase1 = list(date_prices.values())
    sorted_prices_phase1 = sorted(prices_phase1)
    n1 = len(sorted_prices_phase1)
    
    prices_phase2 = [t['price'] for t in all_parsed_tours if t.get('price', 0) > 0]
    sorted_prices_phase2 = sorted(prices_phase2) if prices_phase2 else []
    
    median_phase1 = sorted_prices_phase1[n1 // 2] if n1 > 0 else 0

    date_stats = {
        "all_dates_count": len(all_dates),
        "searched_dates": n1,
        "min_price": min(prices_phase1) if prices_phase1 else 0,
        "max_price": max(prices_phase1) if prices_phase1 else 0,
        "median_price": median_phase1,
        "price_by_date": date_prices,
        "detailed_min_price": min(prices_phase2) if prices_phase2 else 0,
        "detailed_max_price": max(prices_phase2) if prices_phase2 else 0,
        "detailed_tours_count": len(all_parsed_tours)
    }
    
    logging.info(f"ФАЗА 2 завершена. Уникальных отелей: {len(hotels)}")
    
    return {
        "hotels": hotels,
        "date_stats": date_stats
    }


async def direct_deep_search(
    countries: List[Dict],
    start_date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR
) -> Dict[str, any]:
    """
    НОВАЯ ФУНКЦИЯ: Прямой глубокий поиск для точных дат и направлений.
    Ищет на ОДНУ дату вылета по ВСЕМ указанным направлениям.
    
    Args:
        countries: [{"code": "IN", "name": "гоа"}, {"code": "VN", "name": "фукуок"}]
        start_date: "18.05.2026" - дата вылета
        adults: количество взрослых
        nights: количество ночей
        search_type: "tour" или "hotel"
    
    Returns: {
        "hotels": {hotel_key: best_offer},
        "date_stats": {...},
        "search_info": {
            "countries": [...],
            "start_date": "18.05.2026",
            "nights": 7
        }
    }
    """
    logging.info(f"ПРЯМОЙ ПОИСК: {len(countries)} направлений на дату {start_date} ({nights} ночей)")
    
    hotels = {}
    all_parsed_tours = []
    all_prices = []
    
    total_countries = len(countries)
    
    for idx, country in enumerate(countries, 1):
        country_code = country["code"]
        country_name = country["name"]
        destination_slug = country.get("location_slug")
        
        logging.info(f"Парсинг {idx}/{total_countries}: {country_name} на {start_date}")
        
        tours = await deep_parse_date(
            country_code,
            start_date,
            adults,
            nights,
            search_type,
            destination_slug=destination_slug
        )
        
        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower().strip()
            if not hotel_key:
                continue
            
            tour_nights = tour.get('nights', 0)
            if tour_nights > 0 and not nights_match(tour_nights, nights):
                continue
            
            tour['date'] = start_date
            tour['country_code'] = country_code
            tour['country_name'] = country_name
            
            if tour_nights == 0:
                tour['nights'] = nights
            
            all_parsed_tours.append(tour)
            
            if tour.get('price', 0) > 0:
                all_prices.append(tour['price'])
            
            # Группируем по уникальному ключу: отель + страна
            unique_key = f"{hotel_key}_{country_code}"
            
            if unique_key not in hotels:
                hotels[unique_key] = tour
            else:
                if tour['price'] < hotels[unique_key]['price']:
                    hotels[unique_key] = tour
        
        await asyncio.sleep(2)
    
    # Статистика
    sorted_prices = sorted(all_prices) if all_prices else []
    median_price = sorted_prices[len(sorted_prices) // 2] if sorted_prices else 0
    
    date_stats = {
        "all_dates_count": 1,
        "searched_dates": 1,
        "min_price": min(all_prices) if all_prices else 0,
        "max_price": max(all_prices) if all_prices else 0,
        "median_price": median_price,
        "detailed_tours_count": len(all_parsed_tours)
    }
    
    search_info = {
        "countries": [f"{c['name']} ({c['code']})" for c in countries],
        "start_date": start_date,
        "nights": nights
    }
    
    logging.info(f"ПРЯМОЙ ПОИСК завершен. Уникальных отелей: {len(hotels)}, туров: {len(all_parsed_tours)}")
    
    return {
        "hotels": hotels,
        "date_stats": date_stats,
        "search_info": search_info
    }


async def analyze_tours_with_ai(
    hotels: Dict[str, Dict],
    date_stats: Dict,
    params: Dict
) -> List[Dict]:
    """Глубокий AI-анализ с контекстом рынка и развернутыми комментариями."""
    if not hotels:
        return []
    
    tours_list = list(hotels.values())
    tours_list.sort(key=lambda x: x.get("price", 0))
    
    candidates = tours_list[:30]
    
    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    season_info = "Неизвестный сезон"
    if params.get("month"):
        best_months = destination_meta.get("best_months", [])
        season_info = "✅ Отличный сезон" if params["month"] in best_months else "⚠️ Межсезонье/возможны дожди"
    
    prices = [t['price'] for t in candidates]
    ratings = [t['rating'] for t in candidates if t.get('rating', 0) > 0]
    
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
    
    for tour in candidates:
        price = tour['price']
        rating = tour.get('rating', 0)
        
        tour['price_vs_min'] = round((price / market_context['min_price'] - 1) * 100, 1) if market_context['min_price'] else 0
        tour['price_vs_median'] = round((price / market_context['median_price'] - 1) * 100, 1) if market_context['median_price'] else 0
        tour['rating_vs_avg'] = round(rating - market_context['avg_rating'], 1) if rating > 0 else None
    
    # Определяем направления для промпта
    countries_info = params.get("countries", [])
    if countries_info:
        countries_str = ", ".join([c["name"].title() for c in countries_info])
    else:
        countries_str = params.get('country_name', 'направление').title()
    
    # Определяем тип поиска для промпта
    search_type_str = "отели" if params.get("search_type") == SEARCH_TYPE_HOTEL else "туры"
    
    prompt = f"""
Ты — ведущий эксперт-аналитик в сфере туризма. Твоя задача: выбрать 7 лучших предложений из списка 30 кандидатов, проведя кросс-анализ данных.

### КОНТЕКСТ:
• Направления: {countries_str} | {destination_meta.get('description', '')}
• Параметры: {params['adults']} чел, {params['nights']} ночей.
• Статистика рынка: Медиана {market_context['month_median_price']}₽, Средний рейтинг {market_context['avg_rating']}.

### ДАННЫЕ ДЛЯ АНАЛИЗА (JSON):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

### АЛГОРИТМ ПРОВЕРКИ РЕПУТАЦИИ:
Для каждого выбранного варианта проведи виртуальный "cross-check":
1. Сравни `rating` (Level.Travel) с твоими внутренними данными по Booking/Google/TripAdvisor.
2. Определи "Review Density" (Плотность отзывов): Мало (<50), Средне (50-300), Много (300+).
3. **Критическая проверка**: Если рейтинг Level.Travel > 9.0, а на внешних ресурсах < 8.0 — это "High Risk".

### ТРЕБУЕМЫЕ СЦЕНАРИИ (по 1 отелю на каждый):
1. **Low Cost Hero**: Минимальная цена, но рейтинг строго выше 7.5. Без явных рисков.
2. **Value King**: Максимальный балл по формуле (Rating / Price). Идеальный баланс.
3. **Luxury Choice**: Отель 5* с внешним рейтингом > 8.5 и топовым сервисом.
4. **Hidden Gem**: Высокий рейтинг при цене ниже медианы рынка.
5. **Safe Bet**: Отель с "Много отзывов" и стабильной репутацией (совпадение всех рейтингов).
6-7. **Bonus Picks**: На твой выбор (например, лучший пляж или уникальный дизайн).

### ПРАВИЛА JSON:
- Верни ТОЛЬКО массив из 7 объектов.
- В поле `reason` пиши про выгоды и эмоции (25-40 слов).
- В поле `risk_factor` укажи статус (Low/Medium/High) и причину.
- Используй одинарные кавычки ' внутри строк.

[
  {{
    "index": number,
    "ai_score": number (1-10),
    "scenario": "Название",
    "reason": "Текст с цифрами и эмодзи ✈️",
    "external_rating": "Оценка (Источник) / Объем отзывов",
    "risk_factor": "Описание расхождений в рейтингах"
  }}
]
"""

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)
            
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
    
    logging.info("Использую фолбек (без AI)")
    
    good_tours = [t for t in candidates if t.get('rating', 0) >= 6.0]
    if not good_tours:
        good_tours = candidates
    
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
    date_stats: Dict,
    search_info: Optional[Dict] = None
) -> str:
    """Форматирует список туров с расширенной информацией."""
    if not tours:
        return "😢 Туры не найдены"

    # Заголовок
    search_type_emoji = "🏨" if params.get("search_type") == SEARCH_TYPE_HOTEL else "🏖"
    search_type_label = "Отели" if params.get("search_type") == SEARCH_TYPE_HOTEL else "Туры"
    
    if search_info:
        # Режим множественных направлений
        countries_str = ", ".join(search_info["countries"])
        start_date = search_info.get("start_date", "")
        nights = search_info.get("nights", 0)
        
        # Вычисляем дату возвращения
        try:
            start_dt = datetime.strptime(start_date, "%d.%m.%Y")
            end_dt = start_dt + timedelta(days=nights)
            date_display = f"{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}"
        except Exception:
            date_display = start_date
        
        header = (
            f"{search_type_emoji} <b>Топ подборка: {countries_str}</b>\n"
            f"📍 Тип: {search_type_label}\n"
            f"👥 {params['adults']} взр. | 🌙 {nights} ночей\n"
            f"📅 Даты: {date_display}\n\n"
        )
    else:
        # Обычный режим
        country_name = params.get("country_name", "направление").capitalize()
        header = (
            f"{search_type_emoji} <b>Топ подборка: {country_name}</b>\n"
            f"📍 Тип: {search_type_label}\n"
            f"👥 {params['adults']} взр. | 🌙 {params['nights']} ночей\n\n"
        )

    if date_stats:
        header += (
            f"📊 <b>Анализ:</b>\n"
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

        # Направление (если множественный поиск)
        if tour.get("country_name"):
            lines.append(f"🌍 {tour['country_name'].title()}")

        # Диапазон дат
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


async def process_search_command(message: types.Message, command_type: str = "туры"):
    """
    Главный обработчик команды поиска туров/отелей.
    
    Args:
        message: сообщение от пользователя
        command_type: "туры" или "отели"
    
    НОВОЕ:
    - Поддержка команды "отели" (без перелета)
    - Поддержка точных дат: "туры май фукуок 18.05.26-25.05.26"
    - Поддержка множественных направлений: "туры гоа мальдивы шри-ланка"
    - Автоматический выбор режима: месяц (двухфазный) или точные даты (прямой)
    """
    if ADMIN_ID and message.from_user.id != int(ADMIN_ID):
        await message.reply("🚫 Доступ к поиску туров только для администратора.")
        return
    
    try:
        # Определяем тип поиска
        search_type = SEARCH_TYPE_HOTEL if command_type.lower() == "отели" else SEARCH_TYPE_TOUR
        
        params = parse_search_command(message.text, search_type)
        
        if not params.get("countries"):
            await message.reply(
                "❌ Не понял направление. Укажите страну и месяц или точные даты.\n\n"
                "<b>Примеры:</b>\n"
                "• <i>туры апрель шри-ланка 2</i>\n"
                "• <i>туры фукуок 18.05.26-25.05.26</i>\n"
                "• <i>туры гоа мальдивы май 2</i>\n"
                "• <i>отели май гоа</i>\n"
                "• <i>отели фукуок 18.05.26-25.05.26</i>",
                parse_mode="HTML"
            )
            return
        
        # РЕЖИМ 1: Точные даты → прямой глубокий поиск
        if params.get("exact_dates"):
            start_date = params["exact_dates"]["start"]
            end_date = params["exact_dates"]["end"]
            
            countries_str = ", ".join([c["name"].title() for c in params["countries"]])
            search_type_label = "отелей" if search_type == SEARCH_TYPE_HOTEL else "туров"
            
            status_msg = await message.reply(
                f"🔍 <b>Запускаю прямой поиск {search_type_label}</b>\n\n"
                f"📍 Направления: {countries_str}\n"
                f"📅 Дата заезда: {start_date}\n"
                f"🏖 Дата выезда: {end_date}\n"
                f"👥 Взрослых: {params['adults']}\n"
                f"🌙 Ночей: {params['nights']}\n\n"
                f"⏳ Делаю глубокий парсинг по всем направлениям...\n"
                f"Это займет 3-5 минут для каждой страны.",
                parse_mode="HTML"
            )
            
            result = await direct_deep_search(
                countries=params["countries"],
                start_date=start_date,
                adults=params["adults"],
                nights=params["nights"],
                search_type=search_type
            )
            
            hotels = result["hotels"]
            date_stats = result["date_stats"]
            search_info = result["search_info"]
            
            if not hotels:
                await status_msg.edit_text(
                    "😕 Ничего не нашел.\n"
                    "Возможно, нет доступных туров на этот период."
                )
                return
            
            await status_msg.edit_text(
                f"✅ <b>Поиск завершен!</b>\n"
                f"Найдено предложений: {date_stats.get('detailed_tours_count', 0)}\n"
                f"Уникальных отелей: {len(hotels)}\n\n"
                f"⏳ Запускаю AI-анализ...",
                parse_mode="HTML"
            )
            
            # AI анализ (передаем params с countries для правильного промпта)
            best_tours = await analyze_tours_with_ai(hotels, date_stats, params)
            
        # РЕЖИМ 2: Месяц → двухфазный поиск (старая логика)
        else:
            # Если несколько направлений, но без точных дат - ошибка
            if len(params["countries"]) > 1:
                await message.reply(
                    "❌ Для поиска по нескольким направлениям укажите точные даты.\n\n"
                    "<b>Пример:</b>\n"
                    "<i>туры гоа мальдивы 18.05.26-25.05.26</i>",
                    parse_mode="HTML"
                )
                return
            
            country = params["countries"][0]
            params["country_code"] = country["code"]
            params["country_name"] = country["name"]
            
            month_name = list(MONTH_MAPPING.keys())[params.get('month', 1) * 2 - 2] if params.get('month') else 'не указан'
            search_type_label = "отелей" if search_type == SEARCH_TYPE_HOTEL else "туров"
            
            status_msg = await message.reply(
                f"🔍 <b>Запускаю поиск {search_type_label}</b>\n\n"
                f"📍 Направление: {country['name'].title()}\n"
                f"📅 Месяц: весь {month_name}\n"
                f"👥 Взрослых: {params['adults']}\n"
                f"🌙 Ночей: {params['nights']} (ищем {params['nights']-1}-{params['nights']+1})\n\n"
                f"⏳ <b>ФАЗА 1:</b> Быстрое сканирование всех дат месяца...\n"
                f"Это займет 3-5 минут.",
                parse_mode="HTML"
            )
            
            result = await two_phase_search(
                country_code=country["code"],
                month=params.get("month"),
                adults=params["adults"],
                nights=params["nights"],
                search_type=search_type
            )
            
            hotels = result["hotels"]
            date_stats = result["date_stats"]
            search_info = None
            
            if not hotels:
                await status_msg.edit_text(
                    "😕 Ничего не нашел.\n"
                    "Попробуйте другой месяц или направление."
                )
                return
            
            await status_msg.edit_text(
                f"✅ <b>ФАЗА 1 завершена!</b>\n"
                f"Проверено дат: {date_stats.get('searched_dates', 0)}\n"
                f"Найдено предложений: {date_stats.get('detailed_tours_count', 0)}\n"
                f"Уникальных отелей: {len(hotels)}\n\n"
                f"⏳ <b>ФАЗА 2:</b> Запускаю AI-анализ...",
                parse_mode="HTML"
            )
            
            best_tours = await analyze_tours_with_ai(hotels, date_stats, params)
        
        # --- ФОРМИРОВАНИЕ ОТВЕТА (ОБЩЕЕ ДЛЯ ОБОИХ РЕЖИМОВ) ---
        await status_msg.edit_text(
            f"✅ <b>Анализ завершен!</b>\n"
            f"Отобрано {len(best_tours)} лучших предложений\n\n"
            f"⏳ Создаю скриншоты и формирую отчет...",
            parse_mode="HTML"
        )
        
        # Формируем заголовок
        search_type_emoji = "🏨" if search_type == SEARCH_TYPE_HOTEL else "🏖"
        search_type_label = "Отели" if search_type == SEARCH_TYPE_HOTEL else "Туры"
        
        if search_info:
            countries_str = ", ".join(search_info["countries"])
            start_date = search_info.get("start_date", "")
            nights = search_info.get("nights", 0)
            
            # Вычисляем дату возвращения
            try:
                start_dt = datetime.strptime(start_date, "%d.%m.%Y")
                end_dt = start_dt + timedelta(days=nights)
                date_display = f"{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}"
            except Exception:
                date_display = start_date
            
            header = (
                f"{search_type_emoji} <b>Топ подборка: {countries_str}</b>\n"
                f"📍 Тип: {search_type_label}\n"
                f"👥 {params['adults']} взр. | 🌙 {nights} ночей\n"
                f"📅 Даты: {date_display}\n\n"
            )
        else:
            country_name = params.get("country_name", "направление").capitalize()
            header = (
                f"{search_type_emoji} <b>Топ подборка: {country_name}</b>\n"
                f"📍 Тип: {search_type_label}\n"
                f"👥 {params['adults']} взр. | 🌙 {params['nights']} ночей\n\n"
            )
        
        if date_stats:
            header += (
                f"📊 <b>Анализ:</b>\n"
                f"• Проверено дат: {date_stats.get('searched_dates', 0)}\n"
                f"• Минимум: {date_stats.get('min_price', 0):,} ₽\n"
                f"• Медиана: {int(date_stats.get('median_price', 0)):,} ₽\n"
                f"• Максимум: {date_stats.get('max_price', 0):,} ₽\n\n"
                f"📸 В каждом сообщении 2 скриншота:\n"
                f"1. Календарь цен\n"
                f"2. Варианты номеров\n"
            )
        
        await status_msg.delete()
        await message.reply(header, parse_mode="HTML")
        
        # Отправляем каждый тур с альбомом
        for i, tour in enumerate(best_tours, 1):
            try:
                link = tour.get("link", "#")
                name = tour.get("hotel_name", "Отель")
                
                tour_text = f"<b>{i}. <a href='{link}'>{name}</a></b>\n"
                
                if tour.get("scenario"):
                    tour_text += f"🎯 <i>{tour['scenario']}</i>\n"
                
                # Направление (если множественный поиск)
                if tour.get("country_name"):
                    tour_text += f"🌍 {tour['country_name'].title()}\n"
                
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
                
                # Создаем скриншоты
                screenshot_paths = []
                if link and link != "#":
                    screenshot_paths = await capture_hotel_screenshots(link, name, nights, search_type)
                
                # Отправляем
                if screenshot_paths:
                    try:
                        media_group = []
                        for idx, path in enumerate(screenshot_paths):
                            if os.path.exists(path):
                                caption = tour_text if idx == 0 else None
                                media_group.append(
                                    InputMediaPhoto(
                                        media=FSInputFile(path),
                                        caption=caption,
                                        parse_mode="HTML"
                                    )
                                )
                        
                        if media_group:
                            await message.reply_media_group(media=media_group)
                        else:
                            await message.reply(tour_text, parse_mode="HTML", disable_web_page_preview=True)

                        for path in screenshot_paths:
                            if os.path.exists(path):
                                try:
                                    os.remove(path)
                                except Exception:
                                    pass

                    except Exception as e:
                        logging.error(f"Ошибка отправки медиагруппы для {name}: {e}")
                        await message.reply(tour_text, parse_mode="HTML", disable_web_page_preview=True)
                else:
                    await message.reply(tour_text, parse_mode="HTML", disable_web_page_preview=True)
                
                await asyncio.sleep(1.5)
                
            except Exception as e:
                logging.error(f"Критическая ошибка отправки тура #{i}: {e}")
                continue
        
        logging.info(f"Отправлено {len(best_tours)} туров/отелей пользователю {message.from_user.id}")
        
    except Exception as e:
        logging.error(f"Ошибка в process_search_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(e)}")


# Алиасы для обратной совместимости
async def process_tours_command(message: types.Message):
    """Обработчик команды 'туры' (с перелетом)"""
    await process_search_command(message, command_type="туры")


async def process_hotels_command(message: types.Message):
    """Обработчик команды 'отели' (без перелета)"""
    await process_search_command(message, command_type="отели")
