"""Pure parsing and date helpers for the Tutu flight-search feature."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)
MAX_DATE_VARIANTS = 3

MONTH_MAPPING = {
    "январь": 1,
    "января": 1,
    "февраль": 2,
    "февраля": 2,
    "март": 3,
    "марта": 3,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июнь": 6,
    "июня": 6,
    "июль": 7,
    "июля": 7,
    "август": 8,
    "августа": 8,
    "сентябрь": 9,
    "сентября": 9,
    "октябрь": 10,
    "октября": 10,
    "ноябрь": 11,
    "ноября": 11,
    "декабрь": 12,
    "декабря": 12,
}

# Static aliases used both by command parsing and the provider resolver.
CITY_MAPPING = {
    "москва": 491,
    "мск": 491,
    "стамбул": 419,
    "фукуок": 2167,
    "нячанг": 2161,
    "мале": 318,
    "мальдивы": 318,
    "коломбо": 279,
    "шри-ланка": 279,
    "шри ланка": 279,
    "гоа": 199,
    "бали": 2783,
    "питер": 494,
    "санкт-петербург": 494,
    "спб": 494,
    "екатеринбург": 497,
    "казань": 496,
    "сочи": 78,
    "новосибирск": 498,
    "владивосток": 499,
    "калининград": 500,
    "краснодар": 501,
    "самара": 502,
    "уфа": 503,
    "ростов": 504,
    "ростов-на-дону": 504,
    "пермь": 505,
    "красноярск": 506,
    "воронеж": 507,
    "волгоград": 508,
}


def parse_date(date_str: str) -> Optional[str]:
    """Parse DD.MM[.YY|.YYYY] into YYYY-MM-DD."""
    patterns = [
        (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", lambda parts: f"{parts[2]}-{parts[1]:0>2}-{parts[0]:0>2}"),
        (r"(\d{1,2})\.(\d{1,2})\.(\d{2})", lambda parts: f"20{parts[2]}-{parts[1]:0>2}-{parts[0]:0>2}"),
        (r"(\d{1,2})\.(\d{1,2})", lambda parts: None),
    ]

    for pattern, formatter in patterns:
        match = re.search(pattern, date_str)
        if not match:
            continue

        groups = match.groups()
        if len(groups) != 2:
            return formatter(groups)

        day, month = groups
        current_year = datetime.now().year
        try:
            parsed_date = datetime(current_year, int(month), int(day))
            if parsed_date < datetime.now():
                parsed_date = parsed_date.replace(year=current_year + 1)
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def parse_date_range(text: str) -> Optional[Tuple[str, str]]:
    """Parse a DD.MM[.YY]-DD.MM[.YY] range."""
    pattern = r"(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s*-\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)"
    match = re.search(pattern, text)
    if not match:
        return None

    start_str, end_str = match.groups()
    start_date = parse_date(start_str)
    end_date = parse_date(end_str)
    if start_date and end_date:
        return start_date, end_date
    return None


def format_short_date(date_str: str) -> str:
    """Format YYYY-MM-DD as DD.MM, preserving unrecognized input."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return date_str


def format_full_date(date_str: str) -> str:
    """Format YYYY-MM-DD as DD.MM.YYYY, preserving unrecognized input."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return date_str


def parse_search_command(text: str) -> Dict:
    """Parse the user-facing ``билеты`` command into search parameters."""
    text_lower = text.lower().strip()
    if text_lower.startswith("билеты"):
        text_lower = text_lower[6:].strip()

    params = {
        "origins": [],
        "destinations": [],
        "departure_date": None,
        "return_date": None,
        "month": None,
        "passengers": 1,
    }

    date_range = parse_date_range(text_lower)
    if date_range:
        params["departure_date"] = date_range[0]
        params["return_date"] = date_range[1]
        logger.info("Найдены даты: %s - %s", date_range[0], date_range[1])
    else:
        for word in text_lower.split():
            if "." not in word:
                continue
            parsed_date = parse_date(word)
            if parsed_date:
                params["departure_date"] = parsed_date
                break

    if not params["departure_date"]:
        for word in text_lower.split():
            if word in MONTH_MAPPING:
                params["month"] = MONTH_MAPPING[word]
                break

    found_cities = []
    for city_name in CITY_MAPPING:
        if city_name in text_lower and not any(
            city["name"] == city_name for city in found_cities
        ):
            found_cities.append({"name": city_name})

    if not found_cities:
        params["origins"] = [{"name": "москва"}]
    elif len(found_cities) == 1:
        params["origins"] = [{"name": "москва"}]
        params["destinations"] = found_cities
    elif len(found_cities) == 2:
        params["origins"] = [found_cities[0]]
        params["destinations"] = [found_cities[1]]
    else:
        params["origins"] = [{"name": "москва"}]
        params["destinations"] = found_cities

    if not params["departure_date"] and not params["month"]:
        tomorrow = datetime.now() + timedelta(days=1)
        params["departure_date"] = tomorrow.strftime("%Y-%m-%d")

    return params


def generate_month_dates(month: int) -> List[str]:
    """Generate future dates for a calendar month."""
    today = datetime.now()
    year = today.year if month >= today.month else today.year + 1

    dates = []
    day = 1
    while True:
        try:
            candidate_date = datetime(year, month, day)
        except ValueError:
            break
        if candidate_date >= today:
            dates.append(candidate_date.strftime("%Y-%m-%d"))
        day += 1

    return dates


def generate_date_variants(
    departure: str,
    return_date: Optional[str],
) -> List[Tuple[str, Optional[str]]]:
    """Generate up to three ±1 day date variants around the requested trip."""
    dep = datetime.strptime(departure, "%Y-%m-%d")
    ret = datetime.strptime(return_date, "%Y-%m-%d") if return_date else None

    variants = []
    for shift in (-1, 0, 1):
        new_dep = dep + timedelta(days=shift)
        new_ret = ret + timedelta(days=shift) if ret else None
        if new_ret and new_ret <= new_dep:
            continue
        variants.append(
            (
                new_dep.strftime("%Y-%m-%d"),
                new_ret.strftime("%Y-%m-%d") if new_ret else None,
            )
        )

    return variants[:MAX_DATE_VARIANTS]


def build_offer_meta(
    out_date: str,
    return_date: Optional[str],
    requested_out: Optional[str],
    requested_return: Optional[str],
) -> Dict:
    """Build normalized requested/alternative-date metadata for one offer."""
    out_dt = datetime.strptime(out_date, "%Y-%m-%d").date()
    ret_dt = (
        datetime.strptime(return_date, "%Y-%m-%d").date()
        if return_date
        else None
    )

    requested_out = requested_out or out_date
    requested_return = requested_return if requested_return is not None else return_date
    requested_out_dt = datetime.strptime(requested_out, "%Y-%m-%d").date()

    date_type = (
        "exact"
        if out_date == requested_out and return_date == requested_return
        else "alternative"
    )

    date_shift = (out_dt - requested_out_dt).days
    if date_shift < -1:
        date_shift = -1
    elif date_shift > 1:
        date_shift = 1

    return {
        "out_date": out_dt,
        "return_date": ret_dt,
        "date_type": date_type,
        "date_shift": date_shift,
    }
