import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from aiogram import types
from aiogram.types import FSInputFile, InputMediaPhoto
import os

from AI.leveltravel_parsing import (
    DESTINATION_MAPPING,
    MONTH_MAPPING,
    SEARCH_TYPE_HOTEL,
    SEARCH_TYPE_TOUR,
    calculate_nights,
    parse_date_range,
    parse_search_command,
)
from AI.leveltravel_provider import deep_parse_date, quick_price_scan
from AI.leveltravel_ranking import DESTINATION_INFO, analyze_tours_with_ai
from AI.leveltravel_screenshots import capture_hotel_screenshots
from AI.leveltravel_search_plan import (
    LEVELTRAVEL_WEB_URL,
    build_search_url,
    generate_date_range_list,
    generate_full_month_dates,
)
from core.settings import ADMIN_ID


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
