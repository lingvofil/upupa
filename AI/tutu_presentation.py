"""Telegram-facing formatting for Tutu ticket results."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from AI.tutu_parsing import format_full_date, format_short_date


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
