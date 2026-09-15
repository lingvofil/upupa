import asyncio
import logging
from typing import Dict, List, Optional

from AI.leveltravel_parsing import SEARCH_TYPE_TOUR
from AI.leveltravel_provider import deep_parse_date, quick_price_scan
from AI.leveltravel_search_plan import generate_full_month_dates


def nights_match(tour_nights: int, target: int) -> bool:
    """Проверяет, подходит ли количество ночей (с погрешностью ±1)."""
    return target - 1 <= tour_nights <= target + 1


async def two_phase_search(
    country_code: str,
    month: Optional[int],
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
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
        price = await quick_price_scan(
            country_code, date, adults, nights, search_type
        )
        if price:
            date_prices[date] = price
        await asyncio.sleep(1)

    if not date_prices:
        logging.warning("ФАЗА 1: Не найдено ни одной цены")
        return {"hotels": {}, "date_stats": {}}

    sorted_dates = sorted(date_prices.items(), key=lambda x: x[1])
    best_dates = [date for date, price in sorted_dates[:7]]

    logging.info(f"ФАЗА 1 завершена. Лучшие даты: {best_dates}")
    logging.info(
        f"ФАЗА 2: Глубокий парсинг {len(best_dates)} перспективных дат..."
    )

    hotels = {}
    all_parsed_tours = []

    for date in best_dates:
        tours = await deep_parse_date(
            country_code, date, adults, nights, search_type
        )

        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower().strip()
            if not hotel_key:
                continue

            tour_nights = tour.get("nights", 0)
            if tour_nights > 0 and not nights_match(tour_nights, nights):
                continue

            tour["date"] = date
            if tour_nights == 0:
                tour["nights"] = nights

            all_parsed_tours.append(tour)

            if hotel_key not in hotels:
                hotels[hotel_key] = tour
            elif tour["price"] < hotels[hotel_key]["price"]:
                hotels[hotel_key] = tour

        await asyncio.sleep(2)

    prices_phase1 = list(date_prices.values())
    sorted_prices_phase1 = sorted(prices_phase1)
    n1 = len(sorted_prices_phase1)

    prices_phase2 = [
        t["price"] for t in all_parsed_tours if t.get("price", 0) > 0
    ]
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
        "detailed_tours_count": len(all_parsed_tours),
    }

    logging.info(f"ФАЗА 2 завершена. Уникальных отелей: {len(hotels)}")

    return {"hotels": hotels, "date_stats": date_stats}


async def direct_deep_search(
    countries: List[Dict],
    start_date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
) -> Dict[str, any]:
    """Прямой глубокий поиск на одну дату по всем направлениям."""
    logging.info(
        f"ПРЯМОЙ ПОИСК: {len(countries)} направлений на дату "
        f"{start_date} ({nights} ночей)"
    )

    hotels = {}
    all_parsed_tours = []
    all_prices = []
    total_countries = len(countries)

    for idx, country in enumerate(countries, 1):
        country_code = country["code"]
        country_name = country["name"]
        destination_slug = country.get("location_slug")

        logging.info(
            f"Парсинг {idx}/{total_countries}: {country_name} на {start_date}"
        )

        tours = await deep_parse_date(
            country_code,
            start_date,
            adults,
            nights,
            search_type,
            destination_slug=destination_slug,
        )

        for tour in tours:
            hotel_key = tour.get("hotel_name", "").lower().strip()
            if not hotel_key:
                continue

            tour_nights = tour.get("nights", 0)
            if tour_nights > 0 and not nights_match(tour_nights, nights):
                continue

            tour["date"] = start_date
            tour["country_code"] = country_code
            tour["country_name"] = country_name

            if tour_nights == 0:
                tour["nights"] = nights

            all_parsed_tours.append(tour)

            if tour.get("price", 0) > 0:
                all_prices.append(tour["price"])

            unique_key = f"{hotel_key}_{country_code}"
            if unique_key not in hotels:
                hotels[unique_key] = tour
            elif tour["price"] < hotels[unique_key]["price"]:
                hotels[unique_key] = tour

        await asyncio.sleep(2)

    sorted_prices = sorted(all_prices) if all_prices else []
    median_price = (
        sorted_prices[len(sorted_prices) // 2] if sorted_prices else 0
    )

    date_stats = {
        "all_dates_count": 1,
        "searched_dates": 1,
        "min_price": min(all_prices) if all_prices else 0,
        "max_price": max(all_prices) if all_prices else 0,
        "median_price": median_price,
        "detailed_tours_count": len(all_parsed_tours),
    }

    search_info = {
        "countries": [f"{c['name']} ({c['code']})" for c in countries],
        "start_date": start_date,
        "nights": nights,
    }

    logging.info(
        "ПРЯМОЙ ПОИСК завершен. "
        f"Уникальных отелей: {len(hotels)}, туров: {len(all_parsed_tours)}"
    )

    return {
        "hotels": hotels,
        "date_stats": date_stats,
        "search_info": search_info,
    }
