import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from playwright.async_api import async_playwright
from aiogram import types
import json

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
    Скрапит туры с Level.Travel
    
    Args:
        country_code: код страны (например, "lk" для Шри-Ланки)
        dates: список дат вылета
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
            
            try:
                # Формируем URL для поиска
                # Базовый формат Level.Travel с Москвы:
                # https://level.travel/search?country=LK&from=moscow&adults=2&nights_from=7&nights_to=14
                search_params = [
                    f"country={country_code.upper()}",
                    f"from={departure_city}",
                    f"adults={adults}",
                    f"nights_from={nights_from}",
                    f"nights_to={nights_to}"
                ]
                
                # Добавляем курорт/регион, если указан
                if resort:
                    search_params.append(f"resort={resort}")
                
                search_url = f"{LEVELTRAVEL_BASE_URL}/search?{'&'.join(search_params)}"
                
                logging.info(f"Открываю Level.Travel: {search_url}")
                
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                # Ждем загрузки результатов
                try:
                    await page.wait_for_selector('.tour-card, [class*="tour"], [class*="hotel"]', timeout=15000)
                except Exception:
                    logging.warning("Не дождался загрузки карточек туров")
                
                await page.wait_for_timeout(5000)
                
                # Скроллим страницу для подгрузки lazy-load контента
                for _ in range(3):
                    await page.evaluate('window.scrollBy(0, 1000)')
                    await page.wait_for_timeout(1000)
                
                # Извлекаем данные о турах
                tours_data = await page.evaluate("""
                    () => {
                        let results = [];
                        
                        // Ищем карточки туров по различным селекторам
                        const selectors = [
                            '[class*="tour-card"]',
                            '[class*="hotel-card"]',
                            '[class*="SearchResult"]',
                            '[data-testid*="tour"]',
                            '.tour-item',
                            '.hotel-item'
                        ];
                        
                        let cards = [];
                        for (const selector of selectors) {
                            const elements = document.querySelectorAll(selector);
                            if (elements.length > 0) {
                                cards = Array.from(elements);
                                break;
                            }
                        }
                        
                        cards.forEach((card, index) => {
                            try {
                                const tour = {
                                    index: index,
                                    hotel_name: '',
                                    price: 0,
                                    currency: 'RUB',
                                    rating: 0,
                                    reviews_count: 0,
                                    location: '',
                                    stars: 0,
                                    url: '',
                                    departure_date: '',
                                    nights: 0,
                                    meal_type: '',
                                    has_ac: false,
                                    description: ''
                                };
                                
                                // Название отеля
                                const nameEl = card.querySelector('[class*="hotel-name"], [class*="name"], h2, h3');
                                if (nameEl) tour.hotel_name = nameEl.textContent.trim();
                                
                                // Цена (ищем числа)
                                const priceEl = card.querySelector('[class*="price"], [class*="cost"]');
                                if (priceEl) {
                                    const priceText = priceEl.textContent.replace(/\s/g, '');
                                    const priceMatch = priceText.match(/(\d+)/);
                                    if (priceMatch) tour.price = parseInt(priceMatch[1]);
                                }
                                
                                // Рейтинг
                                const ratingEl = card.querySelector('[class*="rating"], [class*="stars"]');
                                if (ratingEl) {
                                    const ratingText = ratingEl.textContent;
                                    const ratingMatch = ratingText.match(/(\d+\.?\d*)/);
                                    if (ratingMatch) tour.rating = parseFloat(ratingMatch[1]);
                                }
                                
                                // Количество отзывов
                                const reviewsEl = card.querySelector('[class*="review"]');
                                if (reviewsEl) {
                                    const reviewsText = reviewsEl.textContent;
                                    const reviewsMatch = reviewsText.match(/(\d+)/);
                                    if (reviewsMatch) tour.reviews_count = parseInt(reviewsMatch[1]);
                                }
                                
                                // Локация
                                const locationEl = card.querySelector('[class*="location"], [class*="city"], [class*="region"]');
                                if (locationEl) tour.location = locationEl.textContent.trim();
                                
                                // Звездность отеля
                                const starsEl = card.querySelector('[class*="stars"]');
                                if (starsEl) {
                                    const starsMatch = starsEl.textContent.match(/(\d+)/);
                                    if (starsMatch) tour.stars = parseInt(starsMatch[1]);
                                }
                                
                                // Ссылка
                                const linkEl = card.querySelector('a[href]');
                                if (linkEl) {
                                    tour.url = linkEl.getAttribute('href');
                                    if (!tour.url.startsWith('http')) {
                                        tour.url = 'https://level.travel' + tour.url;
                                    }
                                }
                                
                                // Дата вылета
                                const dateEl = card.querySelector('[class*="date"], [class*="departure"]');
                                if (dateEl) tour.departure_date = dateEl.textContent.trim();
                                
                                // Количество ночей
                                const nightsEl = card.querySelector('[class*="night"], [class*="duration"]');
                                if (nightsEl) {
                                    const nightsMatch = nightsEl.textContent.match(/(\d+)/);
                                    if (nightsMatch) tour.nights = parseInt(nightsMatch[1]);
                                }
                                
                                // Тип питания
                                const mealEl = card.querySelector('[class*="meal"], [class*="food"]');
                                if (mealEl) tour.meal_type = mealEl.textContent.trim();
                                
                                // Проверка на кондиционер
                                const amenitiesEl = card.querySelector('[class*="amenities"], [class*="facilities"]');
                                if (amenitiesEl) {
                                    const amenitiesText = amenitiesEl.textContent.toLowerCase();
                                    tour.has_ac = amenitiesText.includes('кондиционер') || amenitiesText.includes('ac') || amenitiesText.includes('air');
                                }
                                
                                // Описание
                                const descEl = card.querySelector('[class*="description"], p');
                                if (descEl) tour.description = descEl.textContent.trim();
                                
                                if (tour.hotel_name && tour.price > 0) {
                                    results.push(tour);
                                }
                            } catch (e) {
                                console.error('Ошибка парсинга карточки:', e);
                            }
                        });
                        
                        return results;
                    }
                """)
                
                tours.extend(tours_data)
                logging.info(f"Найдено туров: {len(tours_data)}")
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка при скрапинге Level.Travel: {e}")
    
    # Ограничиваем количество результатов
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
    
    # Формируем промпт для анализа
    month_name = None
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    country_name = params.get("country_name", "неизвестная страна")
    
    prompt = f"""Проанализируй туры в {country_name.capitalize()} {f'в {month_name}' if month_name else ''} и выбери ТОП-10 самых релевантных вариантов.

Критерии отбора (по важности):
1. Положительные отзывы и высокий рейтинг отеля
2. Сезон должен быть комфортным для купания в море (тёплая погода, не сезон дождей)
3. Место должно быть достаточно тусовочным и нескучным (хорошая инфраструктура, развлечения)
4. Наличие кондиционера в номере
5. Оптимальное соотношение цена/качество

Список туров для анализа:
{json.dumps(tours, ensure_ascii=False, indent=2)}

Выведи результат СТРОГО в формате JSON (массив объектов):
[
  {{
    "index": <индекс тура из исходного списка>,
    "score": <оценка от 1 до 10>,
    "reason": "<краткое объяснение, почему этот вариант хорош (1-2 предложения)>"
  }},
  ...
]

Верни ТОЛЬКО JSON, без дополнительного текста."""

    try:
        # Вызываем Groq для анализа
        response = await groq_ai.generate_text(prompt, temperature=0.3)
        
        # Парсим JSON из ответа
        # Ищем JSON в ответе (может быть обернут в markdown)
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            logging.error("Groq не вернул валидный JSON")
            # Возвращаем первые 10 туров без анализа
            return tours[:10]
        
        analysis_results = json.loads(json_match.group(0))
        
        # Сортируем результаты по score
        analysis_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Формируем финальный список туров с анализом
        analyzed_tours = []
        for result in analysis_results[:10]:
            index = result.get("index", 0)
            if 0 <= index < len(tours):
                tour = tours[index].copy()
                tour["ai_score"] = result.get("score", 0)
                tour["ai_reason"] = result.get("reason", "")
                analyzed_tours.append(tour)
        
        return analyzed_tours
        
    except Exception as e:
        logging.error(f"Ошибка при анализе туров через Groq: {e}")
        # Возвращаем первые 10 туров без анализа
        return tours[:10]


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
    
    header = f"🏖 <b>Топ-{len(tours)} туров в {country_name.capitalize()}</b>"
    if month_name:
        header += f" <b>({month_name})</b>"
    header += f"\n👥 {params['adults']} взрослых | 🌙 {params['nights_from']}-{params['nights_to']} ночей\n"
    
    lines = [header]
    
    for i, tour in enumerate(tours, 1):
        lines.append(f"\n<b>{i}. {tour.get('hotel_name', 'Отель')}</b>")
        
        # Основная информация
        details = []
        
        if tour.get('price'):
            details.append(f"💰 {tour['price']:,} {tour.get('currency', 'RUB')}")
        
        if tour.get('stars'):
            details.append(f"⭐️ {'★' * tour['stars']}")
        
        if tour.get('rating'):
            details.append(f"📊 {tour['rating']}/10")
        
        if tour.get('reviews_count'):
            details.append(f"💬 {tour['reviews_count']} отзывов")
        
        if tour.get('location'):
            details.append(f"📍 {tour['location']}")
        
        if tour.get('nights'):
            details.append(f"🌙 {tour['nights']} ночей")
        
        if tour.get('meal_type'):
            details.append(f"🍽 {tour['meal_type']}")
        
        if tour.get('has_ac'):
            details.append("❄️ Кондиционер")
        
        if tour.get('departure_date'):
            details.append(f"📅 {tour['departure_date']}")
        
        if details:
            lines.append(" | ".join(details))
        
        # AI анализ
        if tour.get('ai_score'):
            lines.append(f"🤖 Оценка: {tour['ai_score']}/10")
        
        if tour.get('ai_reason'):
            lines.append(f"💡 {tour['ai_reason']}")
        
        # Ссылка
        if tour.get('url'):
            lines.append(f"🔗 <a href='{tour['url']}'>Подробнее</a>")
    
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
        
        # Скрапим Level.Travel
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
                "• Сайт изменил структуру\n"
                "• Нет доступных предложений\n"
                "• Неверные параметры поиска"
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
