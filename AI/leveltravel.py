import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from playwright.async_api import async_playwright
from aiogram import types
import json

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

LEVELTRAVEL_BASE_URL = "https://level.travel"

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

# Маппинг направлений на коды для Level.Travel
COUNTRY_MAPPING = {
    "северный гоа": ("IN", "north-goa"),
    "гоа": ("IN", "north-goa"),
    "мальдивы": ("MV", None),
    "шри-ланка": ("LK", None),
    "шриланка": ("LK", None),
    "вьетнам": ("VN", None),
    "фукуок": ("VN", "phu-quoc"),
    "нячанг": ("VN", "nha-trang"),
    "нячянг": ("VN", "nha-trang"),
    "турция": ("TR", None),
    "бали": ("ID", "bali"),
    "индонезия": ("ID", "bali"),
}

# Город вылета по умолчанию
DEFAULT_DEPARTURE_CITY = "Moscow-RU"

# Эвристики по направлениям
DESTINATION_INFO = {
    "IN": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3],
        "sea_temp_ok": [11, 12, 1, 2, 3, 4],
        "description": "тусовочное место с пляжами и ночной жизнью"
    },
    "MV": {
        "party": False,
        "best_months": [11, 12, 1, 2, 3, 4],
        "sea_temp_ok": list(range(1, 13)),
        "description": "спокойный пляжный отдых, романтика"
    },
    "LK": {
        "party": False,
        "best_months": [12, 1, 2, 3, 4],
        "sea_temp_ok": [11, 12, 1, 2, 3, 4, 5],
        "description": "пляжи, культура, природа"
    },
    "VN": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3, 4],
        "sea_temp_ok": [1, 2, 3, 4, 5, 11, 12],
        "description": "разнообразный отдых"
    },
    "TR": {
        "party": True,
        "best_months": [5, 6, 7, 8, 9, 10],
        "sea_temp_ok": [5, 6, 7, 8, 9, 10],
        "description": "all inclusive, пляжи, инфраструктура"
    },
    "ID": {
        "party": True,
        "best_months": [4, 5, 6, 7, 8, 9, 10],
        "sea_temp_ok": [4, 5, 6, 7, 8, 9, 10, 11],
        "description": "серфинг, тусовки, культура"
    },
}


def generate_date_range(month: Optional[int] = None) -> List[str]:
    """Генерирует список дат для поиска"""
    dates = []
    today = datetime.now()
    
    if month:
        year = today.year if month >= today.month else today.year + 1
        start_date = datetime(year, month, 1)
        days_in_month = (datetime(year, month + 1, 1) - timedelta(days=1)).day if month < 12 else 31
        
        for day in range(1, days_in_month + 1, 7):  # Каждые 7 дней
            date = datetime(year, month, min(day, days_in_month))
            dates.append(date.strftime("%d.%m.%Y"))
    else:
        # Ближайшие 60 дней
        for i in range(0, 60, 7):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%d.%m.%Y"))
    
    return dates


def parse_tour_command(text: str) -> Dict:
    """Парсит команду типа 'туры апрель шри-ланка 2'"""
    text_lower = text.lower().strip()
    
    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    
    params = {
        "month": None,
        "country": None,
        "country_code": None,
        "resort": None,
        "adults": 2,
        "nights_from": 7,
        "nights_to": 14,
        "departure_city": DEFAULT_DEPARTURE_CITY,
    }
    
    # Ищем месяц
    for word in text_lower.split():
        if word in MONTH_MAPPING:
            params["month"] = MONTH_MAPPING[word]
            break
    
    # Ищем направление
    for destination_name, (country_code, resort_code) in COUNTRY_MAPPING.items():
        if destination_name in text_lower:
            params["country_code"] = country_code
            params["resort"] = resort_code
            params["country_name"] = destination_name
            break
    
    # Количество взрослых
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])
    
    # Продолжительность
    nights_patterns = [
        r'(\d+)-(\d+)\s*(?:ночей|ночи|ночь)',
        r'(\d+)\s*(?:ночей|ночи|ночь)',
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


async def scrape_leveltravel_dom(
    country_code: str,
    date: str,
    adults: int = 2,
    nights: int = 8,
    max_results: int = 50
) -> List[Dict]:
    """
    Парсит туры с Level.Travel через DOM
    
    URL формат: https://level.travel/search/Moscow-RU-to-Any-LK-departure-11.04.2026-for-8-nights-2-adults-0-kids-1..5-stars-package-type
    """
    tours = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            
            try:
                # Формируем URL по паттерну Level.Travel
                search_url = (
                    f"{LEVELTRAVEL_BASE_URL}/search/"
                    f"Moscow-RU-to-Any-{country_code}-"
                    f"departure-{date.replace('.', '.')}-"
                    f"for-{nights}-nights-"
                    f"{adults}-adults-0-kids-"
                    f"1..5-stars-package-type"
                )
                
                logging.info(f"Открываю Level.Travel: {search_url}")
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                # Ждём загрузки контента
                await page.wait_for_timeout(10000)
                
                # Скроллим для подгрузки
                for _ in range(5):
                    await page.evaluate('window.scrollBy(0, 1000)')
                    await page.wait_for_timeout(1500)
                
                # Парсим DOM
                tours_data = await page.evaluate("""
                    () => {
                        let results = [];
                        
                        // Ищем контейнеры с турами
                        const containers = document.querySelectorAll('[class*="hotel"], [class*="tour"], [class*="offer"], article, [data-testid]');
                        
                        containers.forEach((card) => {
                            try {
                                const allText = card.textContent || '';
                                
                                // Фильтр: должна быть цена
                                if (!/\d{4,7}\s*₽/.test(allText)) return;
                                
                                const tour = {
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
                                
                                // Название отеля
                                const nameEl = card.querySelector('h2, h3, h4, [class*="name"], [class*="Name"], [class*="title"]');
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
                                
                                // Дата
                                const dateMatch = allText.match(/(\d{1,2})\s+([а-я]+)/i);
                                if (dateMatch) tour.departure_date = `${dateMatch[1]} ${dateMatch[2]}`;
                                
                                // Питание
                                const mealMatch = allText.match(/(AI|UAI|FB|HB|BB|всё включено|завтрак)/i);
                                if (mealMatch) tour.meal_type = mealMatch[1];
                                
                                // Локация
                                const locationMatch = allText.match(/([А-ЯЁ][а-яё\s]+),\s*([А-ЯЁ][а-яё-]+)/);
                                if (locationMatch) tour.location = `${locationMatch[1].trim()}, ${locationMatch[2]}`;
                                
                                // URL
                                const linkEl = card.querySelector('a[href]');
                                if (linkEl) {
                                    tour.url = linkEl.getAttribute('href');
                                    if (!tour.url.startsWith('http')) tour.url = 'https://level.travel' + tour.url;
                                }
                                
                                if (tour.price > 10000 && (tour.hotel_name || tour.location)) {
                                    results.push(tour);
                                }
                            } catch (e) {}
                        });
                        
                        return results;
                    }
                """)
                
                tours = tours_data[:max_results]
                logging.info(f"Найдено туров через DOM: {len(tours)}")
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка DOM парсинга: {e}")
    
    return tours


async def analyze_tours_with_groq(tours: List[Dict], params: Dict) -> List[Dict]:
    """Анализирует туры через Groq"""
    if not tours:
        return []
    
    # Предфильтрация
    filtered_tours = [t for t in tours if t.get('price', 0) >= 10000 and not (0 < t.get('rating', 0) < 3.5)]
    
    if not filtered_tours:
        filtered_tours = tours
    
    logging.info(f"После предфильтрации: {len(filtered_tours)} туров")
    
    # Эвристики
    destination_key = params.get('country_code')
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    month_name = None
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    country_name = params.get("country_name", "направление")
    
    season_info = ""
    if params.get("month"):
        month_num = params["month"]
        best_months = destination_meta.get('best_months', [])
        
        if month_num in best_months:
            season_info = f"✅ {month_name.capitalize()} - ОТЛИЧНЫЙ сезон"
        else:
            season_info = f"⚠️ {month_name.capitalize()} - не лучший сезон"
    
    party_info = "✅ Тусовочное место" if destination_meta.get('party') else "⚠️ Спокойное место"
    
    prompt = f"""Ты эксперт по турам. Выбери ТОП-10 из списка для {country_name.capitalize()}.

КОНТЕКСТ:
{destination_meta.get('description', '')}
{season_info}
{party_info}

КРИТЕРИИ:
1. Сезонность
2. Рейтинг и отзывы (но у многих рейтинг=0 - это нормально)
3. Цена/качество
4. Звёздность 4-5

ТУРЫ:
{json.dumps(filtered_tours[:30], ensure_ascii=False, indent=2)}

ОТВЕТ (только JSON массив):
[
  {{"index": 0, "score": 8, "reason": "причина 1-2 предложения"}},
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
                if 0 <= idx < len(filtered_tours):
                    tour = filtered_tours[idx].copy()
                    tour["ai_score"] = item.get("score", 0)
                    tour["ai_reason"] = item.get("reason", "")
                    result.append(tour)
            
            return result
    except Exception as e:
        logging.error(f"Ошибка AI анализа: {e}")
    
    # Fallback
    sorted_tours = sorted(filtered_tours, key=lambda x: (x.get('rating', 0), -x.get('price', 999999)), reverse=True)
    return sorted_tours[:10]


def format_tours_message(tours: List[Dict], params: Dict) -> str:
    """Форматирует туры в сообщение"""
    if not tours:
        return "😢 Туры не найдены"
    
    country_name = params.get("country_name", "направление")
    month_name = None
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    header = f"🏖 <b>Топ-{len(tours)}: {country_name.capitalize()}</b>"
    if month_name:
        header += f" <b>({month_name})</b>"
    header += f"\n👥 {params['adults']} взрослых | ✈️ из Москвы\n"
    
    lines = [header]
    
    for i, tour in enumerate(tours, 1):
        lines.append(f"\n<b>{i}. {tour.get('hotel_name', tour.get('location', 'Отель'))}</b>")
        
        details = []
        if tour.get('price'):
            details.append(f"💰 {tour['price']:,} ₽")
        if tour.get('stars'):
            details.append(f"⭐️ {'★' * tour['stars']}")
        if tour.get('rating') and tour['rating'] > 0:
            details.append(f"📊 {tour['rating']}/10")
        if tour.get('reviews_count') and tour['reviews_count'] > 0:
            details.append(f"💬 {tour['reviews_count']}")
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
        
        if tour.get('ai_score'):
            lines.append(f"🤖 AI: {tour['ai_score']}/10")
        if tour.get('ai_reason'):
            lines.append(f"💡 {tour['ai_reason']}")
        if tour.get('url'):
            lines.append(f"🔗 <a href='{tour['url']}'>Подробнее</a>")
    
    return "\n".join(lines)


async def process_tours_command(message: types.Message):
    """Главная функция обработки команды 'туры'"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("🚫 Команда доступна только администратору.")
        return
    
    try:
        params = parse_tour_command(message.text)
        
        if not params.get("country_code"):
            await message.reply(
                "❌ Укажите направление:\n\n"
                "🇮🇳 <b>Северный Гоа</b> | 🇲🇻 <b>Мальдивы</b> | 🇱🇰 <b>Шри-Ланка</b>\n"
                "🇻🇳 <b>Вьетнам</b> | 🏝 <b>Фукуок</b> | 🏖 <b>Нячанг</b>\n"
                "🇹🇷 <b>Турция</b> | 🌴 <b>Бали</b>\n\n"
                "Примеры:\n"
                "<code>туры апрель северный гоа 2</code>\n"
                "<code>туры май шри-ланка</code>",
                parse_mode="HTML"
            )
            return
        
        dates = generate_date_range(params.get("month"))
        
        search_msg = await message.reply(
            f"🔍 Ищу туры: {params.get('country_name', '').title()}\n"
            f"👥 {params['adults']} взрослых | ✈️ из Москвы\n"
            f"Подождите ⏳"
        )
        
        # Используем первую дату и среднее количество ночей
        avg_nights = (params['nights_from'] + params['nights_to']) // 2
        tours = await scrape_leveltravel_dom(
            country_code=params["country_code"],
            date=dates[0],
            adults=params["adults"],
            nights=avg_nights,
            max_results=50
        )
        
        if not tours:
            await search_msg.edit_text(
                "😕 Туры не найдены. Попробуйте:\n"
                "• Другой месяц\n"
                "• Другое направление\n"
                "• Изменить количество взрослых"
            )
            return
        
        await search_msg.edit_text(f"✅ Найдено {len(tours)} туров!\n🤖 Анализирую...")
        
        best_tours = await analyze_tours_with_groq(tours, params)
        result_message = format_tours_message(best_tours, params)
        
        await search_msg.delete()
        await message.reply(result_message, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply(f"❌ Ошибка: {e}", parse_mode="HTML")
