"""Search URL and date planning helpers for LevelTravel."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from AI.leveltravel_parsing import SEARCH_TYPE_HOTEL, SEARCH_TYPE_TOUR


LEVELTRAVEL_WEB_URL = "https://level.travel"


def build_search_url(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None,
) -> str:
    """Build the LevelTravel URL for package or hotel-only search."""
    nights_min = max(1, nights - 1)
    nights_max = nights + 1
    destination_part = destination_slug or f"Any-{country_code}"

    if search_type == SEARCH_TYPE_HOTEL:
        try:
            start_date = datetime.strptime(date, "%d.%m.%Y")
            end_date = start_date + timedelta(days=nights)

            start_min = (start_date - timedelta(days=2)).strftime("%d.%m.%Y")
            start_max = (start_date + timedelta(days=2)).strftime("%d.%m.%Y")
            end_min = (end_date - timedelta(days=2)).strftime("%d.%m.%Y")
            end_max = (end_date + timedelta(days=2)).strftime("%d.%m.%Y")

            return (
                f"{LEVELTRAVEL_WEB_URL}/search/"
                f"Any-RU-to-{destination_part}-"
                f"departure-from-{start_min}..{start_max}-"
                f"to-{end_min}..{end_max}-"
                f"{adults}-adults-0-kids-"
                f"1..5-stars-hotel-type-"
                f"{date}-{end_date.strftime('%d.%m.%Y')}"
            )
        except Exception as exc:
            logging.error(f"Ошибка построения URL для отеля: {exc}")
            return build_search_url(
                country_code,
                date,
                adults,
                nights,
                SEARCH_TYPE_TOUR,
            )

    return (
        f"{LEVELTRAVEL_WEB_URL}/search/"
        f"Moscow-RU-to-{destination_part}-"
        f"departure-{date}-"
        f"for-{nights_min}..{nights_max}-nights-"
        f"{adults}-adults-0-kids-"
        f"1..5-stars-package-type"
    )


def generate_full_month_dates(month: Optional[int] = None) -> List[str]:
    """Generate all future departure dates for a month, or the next 30 days."""
    dates = []
    today = datetime.now()

    if month:
        year = today.year if month >= today.month else today.year + 1
        day = 1
        while True:
            try:
                date = datetime(year, month, day)
                if date >= today:
                    dates.append(date.strftime("%d.%m.%Y"))
                day += 1
            except ValueError:
                break
    else:
        for i in range(1, 31):
            date = today + timedelta(days=i)
            dates.append(date.strftime("%d.%m.%Y"))

    return dates


def generate_date_range_list(start_date: str, end_date: str) -> List[str]:
    """Generate all dates in an inclusive normalized date range."""
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")

        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%d.%m.%Y"))
            current += timedelta(days=1)

        return dates
    except Exception as exc:
        logging.error(f"Ошибка генерации диапазона дат: {exc}")
        return [start_date]
