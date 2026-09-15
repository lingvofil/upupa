from datetime import datetime, timedelta
from typing import Dict, List, Optional

from AI.leveltravel_parsing import SEARCH_TYPE_HOTEL


def format_tours_message(
    tours: List[Dict],
    params: Dict,
    date_stats: Dict,
    search_info: Optional[Dict] = None,
) -> str:
    """Форматирует список туров с расширенной информацией."""
    if not tours:
        return "😢 Туры не найдены"

    search_type_emoji = (
        "🏨" if params.get("search_type") == SEARCH_TYPE_HOTEL else "🏖"
    )
    search_type_label = (
        "Отели" if params.get("search_type") == SEARCH_TYPE_HOTEL else "Туры"
    )

    if search_info:
        countries_str = ", ".join(search_info["countries"])
        start_date = search_info.get("start_date", "")
        nights = search_info.get("nights", 0)

        try:
            start_dt = datetime.strptime(start_date, "%d.%m.%Y")
            end_dt = start_dt + timedelta(days=nights)
            date_display = (
                f"{start_dt.strftime('%d.%m.%Y')} - "
                f"{end_dt.strftime('%d.%m.%Y')}"
            )
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
            f"• Максимум: {date_stats.get('max_price', 0):,} ₽\n"
        )

    lines = [header]

    for i, tour in enumerate(tours, 1):
        link = tour.get("link", "#")
        name = tour.get("hotel_name", "Отель")

        lines.append(f"\n<b>{i}. <a href='{link}'>{name}</a></b>")

        if tour.get("scenario"):
            lines.append(f"🎯 <i>{tour['scenario']}</i>")

        if tour.get("country_name"):
            lines.append(f"🌍 {tour['country_name'].title()}")

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
