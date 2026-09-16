# tutu.py

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional

from aiogram import types

from AI.tutu_parsing import (
    CITY_MAPPING,
    MAX_DATE_VARIANTS,
    MONTH_MAPPING,
    build_offer_meta,
    format_full_date,
    format_short_date,
    generate_date_variants,
    generate_month_dates,
    parse_date,
    parse_date_range,
    parse_search_command,
)
from AI.tutu_provider import (
    CITY_ID_TO_NAME,
    READ_TIMEOUT,
    TUTU_API_URL,
    TUTU_AUTOCOMPLETE_URL,
    TUTU_REFERER,
    TUTU_TIMEOUT,
    fetch_offers,
    get_city_id_from_api,
    multi_destination_search,
    parse_offer,
    resolve_city_id,
    search_tickets,
)
from core.settings import ADMIN_ID
from infrastructure.ai.clients import groq_ai


# Keep the historical public helper/provider surface while implementations live
# in dedicated modules.
_PARSING_REEXPORTS = (
    CITY_MAPPING,
    MAX_DATE_VARIANTS,
    build_offer_meta,
    parse_date,
    parse_date_range,
)
_PROVIDER_REEXPORTS = (
    TUTU_API_URL,
    TUTU_AUTOCOMPLETE_URL,
    TUTU_REFERER,
    READ_TIMEOUT,
    TUTU_TIMEOUT,
    CITY_ID_TO_NAME,
    get_city_id_from_api,
    resolve_city_id,
    fetch_offers,
    parse_offer,
    search_tickets,
)


async def analyze_tickets_with_ai(tickets: List[Dict], params: Dict) -> List[Dict]:
    """AI-анализ билетов с рекомендациями."""
    if not tickets or len(tickets) == 0:
        return []

    candidates = tickets[:20]
    prices = [ticket["price"] for ticket in candidates]

    avg_price = int(sum(prices) / len(prices))
    min_price = min(prices)
    max_price = max(prices)

    origins = params.get("origins", [])
    destinations = params.get("destinations", [])

    origin_str = origins[0]["name"].title() if origins else "неизвестно"
    dest_str = (
        ", ".join(destination["name"].title() for destination in destinations)
        if destinations
        else "неизвестно"
    )

    departure = params.get("departure_date", "")
    return_date = params.get("return_date", "")

    date_info = f"{departure}"
    if return_date:
        date_info += f" - {return_date} (туда-обратно)"

    candidates_simplified = []
    for index, ticket in enumerate(candidates):
        candidates_simplified.append(
            {
                "index": index,
                "price": ticket["price"],
                "airline": ticket["airline"],
                "duration": ticket["duration"],
                "stops": ticket["stops"],
                "baggage": ticket["baggage"],
            }
        )

    prompt = f"""
Ты - профессиональный эксперт по авиабилетам. Проведи анализ и выбери ТОП-7 билетов.

КОНТЕКСТ ПОИСКА:
• Маршрут: {origin_str} → {dest_str}
• Даты: {date_info}
• Пассажиров: {params.get('passengers', 1)}

РЫНОЧНАЯ СТАТИСТИКА:
• Минимальная цена: {min_price:,} ₽
• Максимальная цена: {max_price:,} ₽
• Средняя цена: {avg_price:,} ₽

КАНДИДАТЫ (топ-20 билетов):
{candidates_simplified}

ЗАДАЧА:
Выбери ТОП-7 билетов по разным сценариям:
1. Минимальный бюджет (но адекватные условия)
2. Лучший баланс цена/время
3. Прямой рейс (если есть)
4. С багажом
5. Премиум авиакомпания
6-7. Дополнительные интересные варианты

КРИТЕРИИ:
• Пересадки: 0 - отлично, 1 - нормально, 2+ - плохо
• Авиакомпании: Turkish, Emirates, Qatar - премиум; Аэрофлот, S7 - надежно
• Багаж: важно для многих
• Все цены в РУБЛЯХ

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON массив из 7 объектов:
[
  {{
    "index": 0,
    "ai_score": 9,
    "scenario": "Минимальный бюджет",
    "reason": "S7 Airlines, 15 200 ₽. Прямой рейс 3ч 20м. Отличный вариант для налегке."
  }},
  ...
]

Поля:
• index - номер в массиве candidates (0-19)
• ai_score - оценка 1-10
• scenario - сценарий использования (2-4 слова)
• reason - комментарий (15-40 слов), конкретные факты, эмодзи приветствуются

ВАЖНО: reason должен быть информативным с цифрами и фактами!
"""

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                import json

                ai_results = json.loads(json_match.group(0))
                final_tickets = []
                for item in ai_results:
                    index = item.get("index")
                    if (
                        index is not None
                        and isinstance(index, int)
                        and 0 <= index < len(candidates)
                    ):
                        ticket = candidates[index].copy()
                        ticket["ai_score"] = item.get("ai_score", 0)
                        ticket["scenario"] = item.get("scenario", "Выбор AI")
                        ticket["ai_reason"] = item.get("reason", "Рекомендация AI")
                        final_tickets.append(ticket)

                final_tickets.sort(
                    key=lambda item: item.get("ai_score", 0),
                    reverse=True,
                )

                if final_tickets:
                    logging.info("AI вернул %s рекомендаций", len(final_tickets))
                    return final_tickets

    except Exception as exc:
        logging.error("Ошибка AI анализа: %s", exc)

    logging.info("Использую фолбек (без AI)")

    for ticket in candidates:
        score = 10000 - ticket["price"]
        if ticket["stops"] == 0:
            score += 5000
        elif ticket["stops"] == 1:
            score += 2000

        if ticket["baggage"]:
            score += 1000

        ticket["value_score"] = score

    candidates.sort(key=lambda item: item.get("value_score", 0), reverse=True)
    return candidates[:7]


def format_tickets_message(
    exact_tickets: List[Dict],
    alternative_tickets: List[Dict],
    params: Dict,
    no_exact_message: Optional[str] = None,
) -> str:
    """Форматирует список билетов в HTML для Telegram."""
    if isinstance(params, dict):
        requested_departure = params.get("departure_date")
        requested_return = params.get("return_date")
    else:
        requested_departure = getattr(params, "departure_date", None)
        requested_return = getattr(params, "return_date", None)

    display_departure = (
        requested_departure.strftime("%d.%m.%Y")
        if isinstance(requested_departure, (date, datetime))
        else format_full_date(requested_departure)
        if requested_departure
        else ""
    )
    display_return = (
        requested_return.strftime("%d.%m.%Y")
        if isinstance(requested_return, (date, datetime))
        else format_full_date(requested_return)
        if requested_return
        else ""
    )

    if not exact_tickets and not alternative_tickets:
        return "😢 Билеты не найдены"

    origins = params.get("origins", [])
    destinations = params.get("destinations", [])

    origin_str = origins[0]["name"].title() if origins else "—"
    dest_str = (
        ", ".join(destination["name"].title() for destination in destinations)
        if destinations
        else "—"
    )

    header = f"✈️ <b>Авиабилеты: {origin_str} → {dest_str}</b>\n"
    if requested_return:
        header += f"📅 {display_departure} - {display_return} (туда-обратно)\n"
    else:
        header += f"📅 {display_departure}\n"
    header += f"👥 {params.get('passengers', 1)} пасс.\n\n"

    lines = [header]

    if no_exact_message:
        lines.append(f"❗️{no_exact_message}\n")

    def format_time_block(block: Dict) -> List[str]:
        block_lines = []
        departure_time = block.get("departure", "")
        arrival_time = block.get("arrival", "")
        duration = block.get("duration", "")

        if departure_time and arrival_time:
            def format_datetime(dt_str: str) -> str:
                if "T" in dt_str:
                    date_part, time_part = dt_str.split("T", 1)
                    date_short = format_short_date(date_part)
                    time_short = time_part[:5]
                    return f"{date_short} {time_short}"
                return dt_str

            dep_short = format_datetime(departure_time)
            arr_short = format_datetime(arrival_time)
            if dep_short and arr_short:
                block_lines.append(f"🕒 {dep_short} → {arr_short} ({duration})")

        stops = block.get("stops", 0)
        if stops == 0:
            block_lines.append("✈️ Прямой рейс")
        else:
            block_lines.append(
                f"🔄 {stops} пересадка" if stops == 1 else f"🔄 {stops} пересадки"
            )

        if block.get("baggage"):
            block_lines.append("🧳 Багаж включен")
        else:
            block_lines.append("🧳 Без багажа")

        return block_lines

    def describe_alt_dates(tickets: List[Dict]) -> Optional[str]:
        if not tickets:
            return None
        unique_dates = {
            (
                ticket.get("meta", {}).get("out_date"),
                ticket.get("meta", {}).get("return_date"),
            )
            for ticket in tickets
        }
        unique_dates = {
            (date_out, date_return)
            for date_out, date_return in unique_dates
            if isinstance(date_out, date)
        }
        if len(unique_dates) == 1:
            out_dt, ret_dt = next(iter(unique_dates))
            if ret_dt:
                return f"📅 {out_dt.strftime('%d.%m')} – {ret_dt.strftime('%d.%m')}"
            return f"📅 {out_dt.strftime('%d.%m')}"
        return None

    def render_ticket_block(tickets: List[Dict], start_index: int = 1) -> List[str]:
        block_lines = []
        for index, ticket in enumerate(tickets, start_index):
            link = ticket.get("deeplink", "#")
            airline = ticket.get("airline", "Неизвестно")

            block_lines.append(f"<b>{index}. <a href='{link}'>{airline}</a></b>")

            if ticket.get("scenario"):
                block_lines.append(f"🎯 <i>{ticket['scenario']}</i>")

            trips = ticket.get("trips") or []
            if len(trips) >= 2:
                labels = ["➡️ Туда", "↩️ Обратно"]
                for trip_index, trip in enumerate(trips):
                    label = (
                        labels[trip_index]
                        if trip_index < len(labels)
                        else f"🧭 Сегмент {trip_index + 1}"
                    )
                    block_lines.append(f"<b>{label}</b>")
                    block_lines.extend(format_time_block(trip))
                    if trip_index < len(trips) - 1:
                        block_lines.append("")
            else:
                block_lines.extend(format_time_block(ticket))

            if ticket.get("ai_reason"):
                block_lines.append(f"🤖 <i>{ticket['ai_reason']}</i>")

            price = ticket.get("price", 0)
            currency = ticket.get("currency", "RUB")
            symbol = "₽" if currency == "RUB" else currency
            block_lines.append(f"💰 <b>{price:,} {symbol}</b>\n")
        return block_lines

    if exact_tickets:
        lines.append("🟢 <b>По выбранным датам</b>")
        lines.extend(render_ticket_block(exact_tickets))

    if alternative_tickets:
        if exact_tickets:
            lines.append("────────────────────\n")
            lines.append("🟡 <b>Альтернативные даты</b>")
        else:
            lines.append("🟡 <b>Ближайшие альтернативы</b>")

        alt_date_label = describe_alt_dates(alternative_tickets)
        if alt_date_label:
            lines.append(alt_date_label)
            lines.append("")

        lines.extend(render_ticket_block(alternative_tickets))

    return "\n".join(lines)


async def process_tickets_command(message: types.Message):
    """Главный обработчик команды поиска билетов."""
    if ADMIN_ID and message.from_user.id != int(ADMIN_ID):
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
            month_names = list(MONTH_MAPPING.keys())
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
                await asyncio.sleep(3)
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
                await asyncio.sleep(2)

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
