"""Telegram command orchestration for Tutu flight search."""

from __future__ import annotations

import logging


async def run_tickets_command(
    message,
    *,
    admin_id,
    month_mapping,
    parse_search_command,
    generate_month_dates,
    generate_date_variants,
    multi_destination_search,
    analyze_tickets_with_ai,
    format_tickets_message,
    format_short_date,
    sleep,
):
    """Run the user-facing Tutu command with dependencies supplied by the facade."""
    if admin_id and message.from_user.id != int(admin_id):
        await message.reply("🚫 Доступ к поиску билетов только для администратора.")
        return

    try:
        params = parse_search_command(message.text)

        origins = params.get("origins", [])
        destinations = params.get("destinations", [])

        if not origins or not destinations:
            await message.reply(
                "❌ Не понял направление. Укажите города.\n\n"
                "<b>Примеры:</b>\n"
                "• <i>билеты Сочи</i>\n"
                "• <i>билеты Казань Питер</i>\n"
                "• <i>билеты Дубай 18.05</i>\n"
                "• <i>билеты Пхукет 10.12-25.12</i>\n"
                "• <i>билеты Стамбул май</i>",
                parse_mode="HTML",
            )
            return

        origin_str = ", ".join(origin["name"].title() for origin in origins)
        dest_str = ", ".join(destination["name"].title() for destination in destinations)

        departure = params.get("departure_date", "")
        return_date = params.get("return_date", "")
        month = params.get("month")

        if month:
            month_names = list(month_mapping.keys())
            month_name = month_names[month * 2 - 2].title()

            status_msg = await message.reply(
                "🔍 <b>Запускаю поиск билетов</b>\n\n"
                f"📍 Маршрут: {origin_str} → {dest_str}\n"
                f"📅 Месяц: {month_name}\n"
                f"👥 Пассажиров: {params['passengers']}\n\n"
                "⏳ Сканирую весь месяц...\n"
                "Это может занять 5-10 минут.",
                parse_mode="HTML",
            )

            dates = generate_month_dates(month)[:10]
            all_tickets = []
            for search_date in dates:
                tickets = await multi_destination_search(
                    origins,
                    destinations,
                    search_date,
                    None,
                    params["passengers"],
                    search_date,
                    None,
                )
                all_tickets.extend(tickets)
                await sleep(3)
        else:
            date_info = departure
            if return_date:
                date_info += f" - {return_date} (туда-обратно)"

            status_msg = await message.reply(
                "🔍 <b>Запускаю поиск билетов</b>\n\n"
                f"📍 Маршрут: {origin_str} → {dest_str}\n"
                f"📅 Даты: {date_info}\n"
                f"👥 Пассажиров: {params['passengers']}\n\n"
                "⏳ Ищу лучшие предложения...",
                parse_mode="HTML",
            )

            base_tickets = await multi_destination_search(
                origins,
                destinations,
                departure,
                return_date,
                params["passengers"],
                departure,
                return_date,
            )

            alternative_tickets = []
            date_variants = [
                variant
                for variant in generate_date_variants(departure, return_date)
                if variant != (departure, return_date)
            ]

            for dep_alt, ret_alt in date_variants:
                tickets = await multi_destination_search(
                    origins,
                    destinations,
                    dep_alt,
                    ret_alt,
                    params["passengers"],
                    departure,
                    return_date,
                )
                alternative_tickets.extend(tickets)
                await sleep(2)

            all_tickets = base_tickets + alternative_tickets

        if not all_tickets:
            await status_msg.edit_text(
                "😕 Билеты не найдены.\n"
                "Попробуйте другие даты или направление."
            )
            return

        await status_msg.edit_text(
            "✅ <b>Поиск завершен!</b>\n"
            f"Найдено билетов: {len(all_tickets)}\n\n"
            "⏳ Запускаю AI-анализ...",
            parse_mode="HTML",
        )

        exact_offers = [
            offer
            for offer in all_tickets
            if offer.get("meta", {}).get("date_type") == "exact"
        ]
        alt_offers = [
            offer
            for offer in all_tickets
            if offer.get("meta", {}).get("date_type") == "alternative"
        ]

        best_exact = []
        best_alt = []
        if exact_offers:
            best_exact = await analyze_tickets_with_ai(exact_offers, params)
            best_exact = best_exact[:5]

            if alt_offers:
                best_alt = await analyze_tickets_with_ai(alt_offers, params)
                best_alt = best_alt[:3]
        elif alt_offers:
            best_alt = await analyze_tickets_with_ai(alt_offers, params)
            best_alt = best_alt[:7]

        if not best_exact and not best_alt:
            await status_msg.edit_text("😕 Не удалось проанализировать билеты.")
            return

        await status_msg.delete()

        no_exact_message = None
        if not best_exact and return_date:
            no_exact_message = (
                f"На даты {format_short_date(departure)} – "
                f"{format_short_date(return_date)} билеты не найдены"
            )
        elif not best_exact:
            no_exact_message = (
                f"На дату {format_short_date(departure)} билеты не найдены"
            )

        result_text = format_tickets_message(
            best_exact,
            best_alt,
            params,
            no_exact_message,
        )
        await message.reply(
            result_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        logging.info(
            "Отправлено %s билетов пользователю %s",
            len(best_exact) + len(best_alt),
            message.from_user.id,
        )

    except Exception as exc:
        logging.error("Ошибка в process_tickets_command: %s", exc, exc_info=True)
        await message.reply(f"❌ Произошла ошибка: {str(exc)}")
