import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from playwright.async_api import async_playwright
from aiogram import types
import json

# Импортируем Groq wrapper из config
# Убедитесь, что в config.py есть переменные groq_ai и ADMIN_ID
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


def generate_date_range(month: Optional[int] = None) -> List[str]:
    """Генерирует расширенный список дат для поиска."""
    dates = []
    today = datetime.now()
    
    if month:
        # Если месяц меньше текущего, значит это следующий год
        year = today.year if month >= today.month else today.year + 1
        # Проверяем больше дат для лучшего покрытия
        for day in [1, 5, 10, 15, 20, 25]:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%d.%m.%Y"))
            except ValueError:
                pass
    else:
        # Если месяц не указан, смотрим ближайшие 30 дней с шагом в 5 дней
        for i in range(1, 30, 5):
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
    
    # Поиск количества взрослых (цифра от 1 до 9, не путать с ночами)
    # Ищем одиночную цифру, которая скорее всего кол-во людей
    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        # Если цифра одна, считаем что это взрослые. Если есть "ночей", то это ночи.
        params["adults"] = int(numbers[0])
    
    # Поиск количества ночей (явно указанных)
    nights_match = re.search(r'(\d+)\s*(?:ночей|ночи|ночь|н\b)', text_lower)
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
    Открывает страницу поиска, ждет загрузки и парсит данные из DOM.
    Использует проверенные селекторы для Next.js структуры.
    """
    tours = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # Добавляем аргументы для скрытия автоматизации и устанавливаем русский язык
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow'
            )
            page = await context.new_page()
            
            try:
                # Формируем URL
                search_url = (
                    f"{LEVELTRAVEL_WEB_URL}/search/"
                    f"Moscow-RU-to-Any-{country_code}-"
                    f"departure-{date}-"
                    f"for-{nights}-nights-"
                    f"{adults}-adults-0-kids-"
                    f"1..5-stars-package-type"
                )
                
                logging.info(f"Открываю: {search_url}")
                
                # Переходим на страницу
                await page.goto(search_url, timeout=90000, wait_until='domcontentloaded')
                
                # ВАЖНО: Ждем появления контейнера с карточками отелей
                # Селектор основан на части класса, так как хвосты хэшей меняются
                logging.info("Ожидаю появления карточек...")
                try:
                    await page.wait_for_selector('div[class*="DesktopHotelCard_container"]', timeout=40000)
                except Exception:
                    logging.warning("Карточки не появились за 40 секунд. Возможно, долгая загрузка или нет результатов.")

                # Скролл для подгрузки (Level.Travel использует lazy loading)
                # Скроллим несколько экранов
                for _ in range(7):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(1500)

                # Парсинг DOM
                logging.info("Парсинг DOM...")
                tours_data = await page.evaluate("""
                    () => {
                        const results = [];
                        
                        // Ищем все карточки отелей на странице
                        const cards = Array.from(document.querySelectorAll('div[class*="DesktopHotelCard_container"]'));
                        
                        console.log('JS: Найдено карточек:', cards.length);
                        
                        cards.forEach((card) => {
                            try {
                                const tour = {
                                    hotel_name: 'Без названия',
                                    price: 0,
                                    rating: 0,
                                    stars: 0,
                                    location: '',
                                    link: ''
                                };
                                
                                // 1. Название и Ссылка
                                const titleEl = card.querySelector('a[class*="HotelCardTitle_title"]');
                                if (titleEl) {
                                    tour.hotel_name = titleEl.textContent.trim();
                                    tour.link = titleEl.getAttribute('href');
                                    if (tour.link && !tour.link.startsWith('http')) {
                                        tour.link = 'https://level.travel' + tour.link;
                                    }
                                }
                                
                                // 2. Цена
                                const priceEl = card.querySelector('div[class*="HotelCardPriceBlock_styledPrice"]');
                                if (priceEl) {
                                    // Удаляем пробелы, символы рубля и nbsp
                                    const priceText = priceEl.textContent.replace(/\\s/g, '').replace(/&nbsp;/g, '').replace(/\\u00a0/g, '');
                                    const priceMatch = priceText.match(/(\\d+)/);
                                    if (priceMatch) {
                                        tour.price = parseInt(priceMatch[0]);
                                    }
                                }
                                
                                // 3. Локация
                                const locEl = card.querySelector('p[class*="HotelCardLocation_text"]');
                                if (locEl) {
                                    tour.location = locEl.textContent.trim();
                                }
                                
                                // 4. Рейтинг (может отсутствовать у новых отелей)
                                const ratingEl = card.querySelector('span[class*="HotelRating_rating"]');
                                if (ratingEl) {
                                    tour.rating = parseFloat(ratingEl.textContent.trim());
                                }
                                
                                // 5. Звезды (считаем количество иконок звезд)
                                const starsContainer = card.querySelector('div[class*="HotelStars_container"]');
                                if (starsContainer) {
                                    tour.stars = starsContainer.querySelectorAll('svg').length;
                                }
                                
                                // Фильтрация валидных туров (цена > 1000 чтобы отсеять мусор)
                                if (tour.price > 1000 && tour.hotel_name !== 'Без названия') {
                                    results.push(tour);
                                }
                                
                            } catch (e) {
                                console.error('Ошибка парсинга отдельной карточки:', e);
                            }
                        });
                        
                        return results;
                    }
                """)
                
                tours = tours_data
                logging.info(f"Успешно спарсено: {len(tours)} туров")
                
            except Exception as e:
                logging.error(f"Ошибка внутри браузера: {e}")
                # Для отладки можно раскомментировать сохранение скриншота при ошибке
                # await page.screenshot(path=f"/tmp/error_{date}.png")
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        logging.error(f"Глобальная ошибка Playwright: {e}")
    
    return tours


async def search_tours_multi_date(
    country_code: str,
    dates: List[str],
    adults: int,
    nights: int
) -> List[Dict]:
    """Последовательный поиск по списку дат."""
    all_tours = []
    seen_hotels = set()
    
    # Берем первые 2-3 даты, чтобы не заставлять пользователя ждать вечность
    search_dates = dates[:3] if dates else []
    
    if not search_dates:
        logging.warning("Нет дат для поиска")
        return []

    for date in search_dates:
        logging.info(f"Запускаю поиск на дату: {date}")
        
        tours = await get_tours_hybrid(
            country_code=country_code,
            date=date,
            adults=adults,
            nights=nights
        )
        
        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower()
            # Простая дедупликация по названию отеля
            if hotel_key and hotel_key not in seen_hotels:
                seen_hotels.add(hotel_key)
                tour['date'] = date  # Сохраняем дату вылета для информации
                all_tours.append(tour)
        
        # Если уже набрали достаточно туров (например 30), можно прервать поиск
        if len(all_tours) >= 30:
            logging.info("Набрано достаточно туров, прерываю поиск по датам.")
            break
            
    # Сортируем все найденные туры по цене (от дешевых к дорогим)
    all_tours.sort(key=lambda x: x.get('price', 0))
    
    logging.info(f"Итого уникальных туров найдено: {len(all_tours)}")
    return all_tours


async def analyze_tours_with_groq(tours: List[Dict], params: Dict) -> List[Dict]:
    """
    Отправляет список туров в AI (Groq) для ранжирования и добавления комментариев.
    """
    if not tours:
        return []
    
    # Берем топ-25 самых дешевых для анализа, чтобы не превысить лимиты токенов
    candidates = sorted(tours, key=lambda x: x.get("price", 0))[:25]
    
    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})
    
    season_info = "Неизвестный сезон"
    if params.get("month"):
        best_months = destination_meta.get("best_months", [])
        season_info = "✅ Отличный сезон" if params["month"] in best_months else "⚠️ Межсезонье/Дожди"
    
    prompt = f"""
    Ты - профессиональный турагент. Выбери ТОП-7 лучших предложений из списка JSON ниже для направления {params.get('country_name', 'Курорт')}.
    
    Контекст направления: {destination_meta.get('description', '')}. 
    Сезонность: {season_info}.
    
    Критерии выбора (важно!):
    1. Не выбирай только самые дешевые, если у них ужасный рейтинг (меньше 5).
    2. Приоритет отелям с хорошим соотношением цена/рейтинг.
    3. Разнообразь выбор: включи и бюджетный, и комфортный вариант.

    Входящие данные (JSON):
    {json.dumps(candidates, ensure_ascii=False)}

    Твоя задача вернуть ТОЛЬКО валидный JSON массив объектов с полями:
    - index: (целое число, индекс из исходного массива candidates)
    - ai_score: (число от 1 до 10, твоя оценка привлекательности)
    - ai_reason: (строка, короткий комментарий на русском 3-6 слов, почему выбрал, используй эмодзи)
    """

    try:
        if groq_ai:
            # ИСПРАВЛЕНИЕ ЗДЕСЬ: Убрал аргумент temperature, так как ваша версия либы его не поддерживает
            response = await groq_ai.generate_text(prompt)
            
            # Пытаемся найти JSON в ответе (иногда AI пишет текст до или после JSON)
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                ai_results = json.loads(json_match.group(0))
                
                final_tours = []
                for item in ai_results:
                    idx = item.get('index')
                    if idx is not None and isinstance(idx, int) and 0 <= idx < len(candidates):
                        tour = candidates[idx].copy()
                        tour['ai_score'] = item.get('ai_score', 0)
                        tour['ai_reason'] = item.get('ai_reason', 'Выбор AI')
                        final_tours.append(tour)
                
                # Сортируем итоговую подборку по оценке AI
                final_tours.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
                
                if final_tours:
                    return final_tours

    except Exception as e:
        logging.error(f"Ошибка AI анализа: {e}")
    
    # Фолбек (если AI сломался или вернул пустоту):
    # Возвращаем просто отсортированные по рейтингу, но не слишком дорогие
    logging.info("Использую фолбек сортировку (без AI)")
    filtered_fallback = [t for t in candidates if t.get('rating', 0) > 6]
    if not filtered_fallback:
        filtered_fallback = candidates
    return sorted(filtered_fallback, key=lambda x: x.get("price", 0))[:7]


def format_tours_message(tours: List[Dict], params: Dict) -> str:
    """Форматирует список туров в читаемое сообщение Telegram."""
    if not tours:
        return "😢 Туры не найдены"
    
    country_name = params.get("country_name", "направление").capitalize()
    
    header = f"🏖 <b>Топ подборка: {country_name}</b>\n"
    header += f"👥 {params['adults']} взр. | 🌙 {params['nights']} ночей\n"
    
    lines = [header]
    
    for i, tour in enumerate(tours, 1):
        # Название и ссылка
        link = tour.get('link', '#')
        name = tour.get('hotel_name', 'Отель')
        lines.append(f"\n<b>{i}. <a href='{link}'>{name}</a></b>")
        
        # Инфострока
        stars = "⭐️" * tour.get('stars', 0)
        rating = tour.get('rating', 0)
        rating_str = f"📊 {rating}" if rating > 0 else ""
        date_str = f"📅 {tour.get('date', '')}"
        
        meta_parts = [p for p in [stars, rating_str, date_str] if p]
        if meta_parts:
            lines.append(" | ".join(meta_parts))
            
        # Локация
        if tour.get('location'):
            lines.append(f"📍 {tour['location']}")
            
        # AI мнение (если есть)
        if tour.get('ai_reason'):
            lines.append(f"🤖 <i>{tour['ai_reason']}</i>")
            
        # Цена
        price = tour.get('price', 0)
        lines.append(f"💰 <b>{price:,} ₽</b>")
    
    return "\n".join(lines)


async def process_tours_command(message: types.Message):
    """Обработчик команды из бота."""
    # Проверка на админа (если ADMIN_ID задан)
    if ADMIN_ID and message.from_user.id != int(ADMIN_ID):
        await message.reply("🚫 Доступ к поиску туров только для администратора.")
        return
    
    try:
        params = parse_tour_command(message.text)
        
        if not params.get("country_code"):
            await message.reply(
                "❌ Не понял направление. Укажите страну, месяц и кол-во людей.\n"
                "Пример: <i>туры апрель шри-ланка 2</i>",
                parse_mode="HTML"
            )
            return
        
        # Генерируем даты
        dates = generate_date_range(params.get("month"))
        
        status_msg = await message.reply(
            f"🔍 Ищу туры: {params.get('country_name', '').title()}...\n"
            f"Проверяю даты: {', '.join(dates[:3])}...\n"
            f"⏳ Это займет около 30-60 секунд."
        )
        
        # 1. Поиск
        tours = await search_tours_multi_date(
            country_code=params["country_code"],
            dates=dates,
            adults=params["adults"],
            nights=params["nights"]
        )
        
        if not tours:
            await status_msg.edit_text(
                "😕 Ничего не нашел.\n"
                "Возможно, слишком далекая дата или проблемы на сайте.\n"
                "Попробуйте изменить месяц."
            )
            return
        
        await status_msg.edit_text(f"✅ Нашел {len(tours)} вариантов. Запускаю AI анализ...")
        
        # 2. Анализ (AI)
        best_tours = await analyze_tours_with_groq(tours, params)
        
        # 3. Форматирование и отправка
        text_response = format_tours_message(best_tours, params)
        
        # Удаляем сообщение о статусе и отправляем результат
        await status_msg.delete()
        await message.reply(text_response, parse_mode="HTML", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Error in process_tours_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка при поиске: {str(e)}")

if __name__ == "__main__":
    # Для локального теста без бота
    async def test():
        print("Запуск теста...")
        tours = await get_tours_hybrid("LK", "01.04.2026", 2, 10)
        print(f"Найдено: {len(tours)}")
        for t in tours[:3]:
            print(t)
            
    asyncio.run(test())
