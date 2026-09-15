"""Pure parsing and normalization for LevelTravel search commands."""

import logging
import re
from datetime import datetime
from typing import Dict, Optional, Tuple


SEARCH_TYPE_TOUR = "tour"
SEARCH_TYPE_HOTEL = "hotel"

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

DESTINATION_MAPPING = {
    "северный гоа": {"country_code": "IN", "location_slug": None},
    "гоа": {"country_code": "IN", "location_slug": None},
    "мальдивы": {"country_code": "MV", "location_slug": None},
    "шри-ланка": {"country_code": "LK", "location_slug": None},
    "шриланка": {"country_code": "LK", "location_slug": None},
    "вьетнам": {"country_code": "VN", "location_slug": None},
    "фукуок": {"country_code": "VN", "location_slug": "Phu.Quoc-VN"},
    "нячанг": {"country_code": "VN", "location_slug": "Nha.Trang-VN"},
    "турция": {"country_code": "TR", "location_slug": None},
    "бали": {"country_code": "ID", "location_slug": None},
    "индонезия": {"country_code": "ID", "location_slug": None},
    "таиланд": {"country_code": "TH", "location_slug": None},
    "пхукет": {"country_code": "TH", "location_slug": None},
    "паттайя": {"country_code": "TH", "location_slug": None},
    "оаэ": {"country_code": "AE", "location_slug": None},
    "дубай": {"country_code": "AE", "location_slug": None},
    "египет": {"country_code": "EG", "location_slug": None},
    "хургада": {"country_code": "EG", "location_slug": None},
    "шарм": {"country_code": "EG", "location_slug": None},
}


def parse_date_range(text: str) -> Optional[Tuple[str, str]]:
    """Parse a supported date range and normalize it to ``DD.MM.YYYY``."""
    pattern_full = r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})'
    pattern_short = r'(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})'

    match_full = re.search(pattern_full, text)
    if match_full:
        d1, m1, y1, d2, m2, y2 = match_full.groups()
        y1 = int(y1) if len(y1) == 4 else 2000 + int(y1)
        y2 = int(y2) if len(y2) == 4 else 2000 + int(y2)

        try:
            start = datetime(y1, int(m1), int(d1))
            end = datetime(y2, int(m2), int(d2))
            return (start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y"))
        except ValueError:
            return None

    match_short = re.search(pattern_short, text)
    if match_short:
        d1, m1, d2, m2 = match_short.groups()
        current_year = datetime.now().year

        try:
            start = datetime(current_year, int(m1), int(d1))
            end = datetime(current_year, int(m2), int(d2))

            if start < datetime.now():
                start = start.replace(year=current_year + 1)
                end = end.replace(year=current_year + 1)

            return (start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y"))
        except ValueError:
            return None

    return None


def calculate_nights(start_date: str, end_date: str) -> int:
    """Return the number of nights between normalized dates."""
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
        return (end - start).days
    except Exception:
        return 7


def parse_search_command(text: str, search_type: str = SEARCH_TYPE_TOUR) -> Dict:
    """Parse a ``туры``/``отели`` command into normalized search parameters."""
    text_lower = text.lower().strip()

    if text_lower.startswith("туры"):
        text_lower = text_lower[4:].strip()
    elif text_lower.startswith("отели"):
        text_lower = text_lower[5:].strip()

    params = {
        "month": None,
        "countries": [],
        "adults": 2,
        "nights": 10,
        "exact_dates": None,
        "search_type": search_type,
    }

    date_range = parse_date_range(text_lower)
    if date_range:
        params["exact_dates"] = {"start": date_range[0], "end": date_range[1]}
        params["nights"] = calculate_nights(date_range[0], date_range[1])
        logging.info(
            "Найдены точные даты: %s - %s (%s ночей)",
            date_range[0],
            date_range[1],
            params["nights"],
        )

    nights_match = re.search(r'(\d+)\s*(?:ночей|ночи|ночь|н\b)', text_lower)
    if nights_match and not params["exact_dates"]:
        params["nights"] = int(nights_match.group(1))
        text_lower = text_lower.replace(nights_match.group(0), "")

    if not params["exact_dates"]:
        for word in text_lower.split():
            if word in MONTH_MAPPING:
                params["month"] = MONTH_MAPPING[word]
                break

    for dest_name in sorted(DESTINATION_MAPPING.keys(), key=len, reverse=True):
        if dest_name not in text_lower:
            continue

        dest_meta = DESTINATION_MAPPING[dest_name]
        code = dest_meta["country_code"]
        location_slug = dest_meta.get("location_slug")

        if location_slug is None and any(
            country["code"] == code and country.get("location_slug")
            for country in params["countries"]
        ):
            continue

        if not any(
            country["code"] == code and country.get("location_slug") == location_slug
            for country in params["countries"]
        ):
            params["countries"].append(
                {
                    "code": code,
                    "name": dest_name,
                    "location_slug": location_slug,
                }
            )

    numbers = re.findall(r'\b([1-9])\b', text_lower)
    if numbers:
        params["adults"] = int(numbers[0])

    return params
