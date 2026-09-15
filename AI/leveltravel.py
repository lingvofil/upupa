import asyncio
import logging
import os

from aiogram import types
from aiogram.types import FSInputFile, InputMediaPhoto

from AI.leveltravel_parsing import (
    DESTINATION_MAPPING,
    MONTH_MAPPING,
    SEARCH_TYPE_HOTEL,
    SEARCH_TYPE_TOUR,
    calculate_nights,
    parse_date_range,
    parse_search_command,
)
from AI.leveltravel_presentation import (
    format_search_header,
    format_tour_card,
    format_tours_message,
)
from AI.leveltravel_provider import deep_parse_date, quick_price_scan
from AI.leveltravel_ranking import DESTINATION_INFO, analyze_tours_with_ai
from AI.leveltravel_screenshots import capture_hotel_screenshots
from AI.leveltravel_search import direct_deep_search, nights_match, two_phase_search
from AI.leveltravel_search_plan import (
    LEVELTRAVEL_WEB_URL,
    build_search_url,
    generate_date_range_list,
    generate_full_month_dates,
)
from core.settings import ADMIN_ID


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
        search_type = (
            SEARCH_TYPE_HOTEL
            if command_type.lower() == "отели"
            else SEARCH_TYPE_TOUR
        )

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
                parse_mode="HTML",
            )
            return

        if params.get("exact_dates"):
            start_date = params["exact_dates"]["start"]
            end_date = params["exact_dates"]["end"]

            countries_str = ", ".join(
                [c["name"].title() for c in params["countries"]]
            )
            search_type_label = (
                "отелей" if search_type == SEARCH_TYPE_HOTEL else "туров"
            )

            status_msg = await message.reply(
                f"🔍 <b>Запускаю прямой поиск {search_type_label}</b>\n\n"
                f"📍 Направления: {countries_str}\n"
                f"📅 Дата заезда: {start_date}\n"
                f"🏖 Дата выезда: {end_date}\n"
                f"👥 Взрослых: {params['adults']}\n"
                f"🌙 Ночей: {params['nights']}\n\n"
                f"⏳ Делаю глубокий парсинг по всем направлениям...\n"
                f"Это займет 3-5 минут для каждой страны.",
                parse_mode="HTML",
            )

            result = await direct_deep_search(
                countries=params["countries"],
                start_date=start_date,
                adults=params["adults"],
                nights=params["nights"],
                search_type=search_type,
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
                parse_mode="HTML",
            )

            best_tours = await analyze_tours_with_ai(hotels, date_stats, params)
        else:
            if len(params["countries"]) > 1:
                await message.reply(
                    "❌ Для поиска по нескольким направлениям укажите точные даты.\n\n"
                    "<b>Пример:</b>\n"
                    "<i>туры гоа мальдивы 18.05.26-25.05.26</i>",
                    parse_mode="HTML",
                )
                return

            country = params["countries"][0]
            params["country_code"] = country["code"]
            params["country_name"] = country["name"]

            month_name = (
                list(MONTH_MAPPING.keys())[params.get("month", 1) * 2 - 2]
                if params.get("month")
                else "не указан"
            )
            search_type_label = (
                "отелей" if search_type == SEARCH_TYPE_HOTEL else "туров"
            )

            status_msg = await message.reply(
                f"🔍 <b>Запускаю поиск {search_type_label}</b>\n\n"
                f"📍 Направление: {country['name'].title()}\n"
                f"📅 Месяц: весь {month_name}\n"
                f"👥 Взрослых: {params['adults']}\n"
                f"🌙 Ночей: {params['nights']} "
                f"(ищем {params['nights']-1}-{params['nights']+1})\n\n"
                f"⏳ <b>ФАЗА 1:</b> Быстрое сканирование всех дат месяца...\n"
                f"Это займет 3-5 минут.",
                parse_mode="HTML",
            )

            result = await two_phase_search(
                country_code=country["code"],
                month=params.get("month"),
                adults=params["adults"],
                nights=params["nights"],
                search_type=search_type,
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
                parse_mode="HTML",
            )

            best_tours = await analyze_tours_with_ai(hotels, date_stats, params)

        await status_msg.edit_text(
            f"✅ <b>Анализ завершен!</b>\n"
            f"Отобрано {len(best_tours)} лучших предложений\n\n"
            f"⏳ Создаю скриншоты и формирую отчет...",
            parse_mode="HTML",
        )

        header = format_search_header(
            params,
            date_stats,
            search_info,
            include_screenshot_note=True,
        )

        await status_msg.delete()
        await message.reply(header, parse_mode="HTML")

        for i, tour in enumerate(best_tours, 1):
            try:
                link = tour.get("link", "#")
                name = tour.get("hotel_name", "Отель")
                nights = tour.get("nights", params.get("nights", 0))
                tour_text = format_tour_card(tour, i, params)

                screenshot_paths = []
                if link and link != "#":
                    screenshot_paths = await capture_hotel_screenshots(
                        link,
                        name,
                        nights,
                        search_type,
                    )

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
                                        parse_mode="HTML",
                                    )
                                )

                        if media_group:
                            await message.reply_media_group(media=media_group)
                        else:
                            await message.reply(
                                tour_text,
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                            )

                        for path in screenshot_paths:
                            if os.path.exists(path):
                                try:
                                    os.remove(path)
                                except Exception:
                                    pass

                    except Exception as e:
                        logging.error(
                            f"Ошибка отправки медиагруппы для {name}: {e}"
                        )
                        await message.reply(
                            tour_text,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                else:
                    await message.reply(
                        tour_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )

                await asyncio.sleep(1.5)

            except Exception as e:
                logging.error(f"Критическая ошибка отправки тура #{i}: {e}")
                continue

        logging.info(
            f"Отправлено {len(best_tours)} туров/отелей пользователю "
            f"{message.from_user.id}"
        )

    except Exception as e:
        logging.error(f"Ошибка в process_search_command: {e}", exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(e)}")


async def process_tours_command(message: types.Message):
    """Обработчик команды 'туры' (с перелетом)"""
    await process_search_command(message, command_type="туры")


async def process_hotels_command(message: types.Message):
    """Обработчик команды 'отели' (без перелета)"""
    await process_search_command(message, command_type="отели")
