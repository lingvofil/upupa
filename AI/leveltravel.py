import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from playwright.async_api import async_playwright
from aiogram import types
import json
import httpx

# Импортируем Groq wrapper из config
from config import groq_ai, ADMIN_ID

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

LEVELTRAVEL_WEB_URL = "https://level.travel"
LEVELTRAVEL_API_URL = "https://api.level.travel"

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
}

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
    """Генерирует даты"""
    dates = []
    today = datetime.now()
    
    if month:
        year = today.year if month >= today.month else today.year + 1
        for day in [1, 8, 15]:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%d.%m.%Y"))
            except ValueError:
                pass
    else:
        for i in range(0, 30, 7):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%d.%m.%Y"))
    
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
        "nights": 10,
    }
    
    # Месяц
    for word in text_lower.split():
        if word in MONTH_MAPPING:
            params["month"] = MONTH_MAPPING[word]
            break
    
    # Направление
    for dest_name, code in COUNTRY_MAPPING.items():
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
        params["nights"] = int(nights_match.group(1))
    
    return params


async def get_tours_hybrid(
    country_code: str,
    date: str,
    adults: int,
    nights: int
) -> List[Dict]:
    """
    ГИБРИДНЫЙ ПОДХОД:
    1. Playwright открывает страницу поиска
    2. Перехватываем request_id из Network
    3. Парсим DOM после загрузки
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
                # URL Level.Travel
                search_url = (
                    f"{LEVELTRAVEL_WEB_URL}/search/"
                    f"Moscow-RU-to-Any-{country_code}-"
                    f"departure-{date}-"
                    f"for-{nights}-nights-"
                    f"{adults}-adults-0-kids-"
                    f"1..5-stars-package-type"
                )
                
                logging.info(f"Открываю: {search_url}")
                
                await page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                
                # Ждём загрузки контента
                logging.info("Ожидаю загрузку туров...")
                await page.wait_for_timeout(15000)
                
                # Скроллим
                for i in range(10):
                    await page.evaluate('window.scrollBy(0, 800)')
                    await page.wait_for_timeout(1000)
                
                # Парсим DOM
                logging.info("Парсинг DOM...")
                tours_data = await page.evaluate("""
                    () => {
                        const results = [];
                        
                        // Ищем все возможные контейнеры
                        const selectors = [
                            '[data-testid*="hotel"]',
                            '[data-testid*="tour"]',
                            '[class*="HotelCard"]',
                            '[class*="TourCard"]',
                            'article',
                            '[class*="hotel"]',
                            '[class*="offer"]'
                        ];
                        
                        let cards = [];
                        for (const sel of selectors) {
                            cards = Array.from(document.querySelectorAll(sel));
                            if (cards.length > 0) break;
                        }
                        
                        // Fallback: любые div с ценой
                        if (cards.length === 0) {
                            const allDivs = Array.from(document.querySelectorAll('div'));
                            cards = allDivs.filter(div => {
                                const text = div.textContent || '';
                                return /\\d{4,7}\\s*₽/.test(text) && div.querySelectorAll('*').length > 5;
                            });
                        }
                        
                        console.log('Найдено карточек:', cards.length);
                        
                        cards.forEach((card) => {
                            try {
                                const text = card.textContent || '';
                                
                                // Фильтр: должна быть цена
                                if (!/\\d{4,7}\\s*₽/.test(text)) return;
                                
                                const tour = {
                                    hotel_name: '',
                                    price: 0,
                                    rating: 0,
                                    reviews_count: 0,
                                    stars: 0,
                                    nights: 0,
                                    location: '',
                                    meal_type: '',
                                    url: ''
                                };
                                
                                // Название
                                const nameEl = card.querySelector('h1, h2, h3, h4, [class*="name"], [class*="Name"], [class*="title"], [class*="Title"]');
                                if (nameEl) {
                                    tour.hotel_name = nameEl.textContent.trim();
                                }
                                
                                // Цена
                                const priceMatch = text.match(/(\\d{4,7})\\s*₽/);
                                if (priceMatch) {
                                    tour.price = parseInt(priceMatch[1]);
                                }
                                
                                // Рейтинг
                                const ratingMatch = text.match(/(\\d\\.?\\d?)\\s*\\/\\s*10/);
                                if (ratingMatch) {
                                    tour.rating = parseFloat(ratingMatch[1]);
                                }
                                
                                // Отзывы
                                const reviewMatch = text.match(/(\\d+)\\s*отзыв/i);
                                if (reviewMatch) {
                                    tour.reviews_count = parseInt(reviewMatch[1]);
                                }
                                
                                // Звёзды
                                const starsMatch = text.match(/(\\d)\\s*(?:звезд|★)/i);
                                if (starsMatch) {
                                    tour.stars = parseInt(starsMatch[1]);
                                }
                                
                                // Ночи
                                const nightsMatch = text.match(/(\\d+)\\s*(?:ночей|ночи)/i);
                                if (nightsMatch) {
                                    tour.nights = parseInt(nightsMatch[1]);
                                }
                                
                                // Питание
                                const mealMatch = text.match(/(AI|UAI|FB|HB|BB|всё включено|завтрак)/i);
                                if (mealMatch) {
                                    tour.meal_type = mealMatch[1];
                                }
                                
                                // Локация
                                const locationMatch = text.match(/([А-ЯЁ][а-яё\\s]+),\\s*([А-ЯЁ][а-яё-]+)/);
                                if (locationMatch) {
                                    tour.location = `${locationMatch[1].trim()}, ${locationMatch[2]}`;
                                }
                                
                                // URL
                                const linkEl = card.querySelector('a[href]');
                                if (linkEl) {
                                    tour.url = linkEl.getAttribute('href');
                                    if (tour.url && !tour.url.startsWith('http')) {
                                        tour.url = 'https://level.travel' + tour.url;
                                    }
                                }
                                
                                // Добавляем если есть название или цена
                                if ((tour.hotel_name || tour.location) && tour.price >= 10000) {
                                    results.push(tour);
                                }
                            } catch (e) {
                                console.error('Parse error:', e);
                            }
                        });
                        
                        console.log('Спарсено туров:', results.length);
                        return results;
                    }
                """)
                
                tours = tours_data
                logging.info(f"Найдено туров: {len(tours)}")
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Ошибка: {e}")
    
    return tours


async def search_tours_multi_date(
    country_code: str,
    dates: List[str],
    adults: int,
    nights: int
) -> List[Dict]:
    """Поиск по нескольким датам"""
    all_tours = []
    seen_hotels = set()
    
    # Максимум 2 даты для скорости
    for date in dates[:2]:
        logging.info(f"Поиск на дату: {date}")
        
        tours = await get_tours_hybrid(
            country_code=country_code,
            date=date,
            adults=adults,
            nights=nights
        )
        
        # Дедупликация
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
    
    filtered = [t for t in tours if t.get("price", 0) >= 10000]
    
    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    month_name = ""
    if params.get("month"):
        month_name = [k for k, v in MONTH_MAPPING.items() if v == params["month"] and len(k) > 3][0]
    
    season_info = ""
    if params.get("month"):
        best_months = destination_meta.get("best_months", [])
        season_info = "✅ Отличный сезон" if params["month"] in best_months else "⚠️ Не лучший сезон"
    
    party_info = "✅ Тусовки" if destination_meta.get("party") else "⚠️ Спокойно"
    
    prompt = f"""Топ-10 туров для {params.get('country_name', '').capitalize()}.

КОНТЕКСТ:
{destination_meta.get('description')}
{season_info}
{party_info}

КРИТЕРИИ:
1. Сезонность
2. Рейтинг (0 = нет данных, это норма)
3. Цена/качество
4. Звёзды 4-5

ТУРЫ:
{json.dumps(filtered[:30], ensure_ascii=False, indent=2)}

JSON:
[
  {{"index": 0, "score": 8, "reason": "краткая причина"}},
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
        logging.error(f"AI error: {e}")
    
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
            f"⏳ Подождите 30-40 сек..."
        )
        
        tours = await search_tours_multi_date(
            country_code=params["country_code"],
            dates=dates,
            adults=params["adults"],
            nights=params["nights"]
        )
        
        if not tours:
            await search_msg.edit_text(
                "😕 Туры не найдены.\n\n"
                "Попробуйте:\n"
                "• Другой месяц\n"
                "• Другое направление\n"
                "• Изменить параметры"
            )
            return
        
        await search_msg.edit_text(f"✅ {len(tours)} туров!\n🤖 Анализирую...")
        
        best_tours = await analyze_tours_with_groq(tours, params)
        result = format_tours_message(best_tours, params)
        
        await search_msg.delete()
        await message.reply(result, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await message.reply(f"❌ Ошибка: {e}")
