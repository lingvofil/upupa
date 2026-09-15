import asyncio
import logging
import os

from aiogram.types import FSInputFile, InputMediaPhoto

from AI.leveltravel_presentation import format_search_header, format_tour_card
from AI.leveltravel_screenshots import capture_hotel_screenshots


async def send_search_results(
    message,
    status_msg,
    best_tours,
    params,
    date_stats,
    search_info,
    search_type,
) -> None:
    """Отправляет итоговую подборку и связанные скриншоты в Telegram."""
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

                except Exception as exc:
                    logging.error(
                        f"Ошибка отправки медиагруппы для {name}: {exc}"
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

        except Exception as exc:
            logging.error(f"Критическая ошибка отправки тура #{i}: {exc}")
            continue

    logging.info(
        f"Отправлено {len(best_tours)} туров/отелей пользователю "
        f"{message.from_user.id}"
    )
