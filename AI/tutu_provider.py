"""Tutu provider access, offer parsing, and search orchestration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from AI.tutu_parsing import CITY_MAPPING, build_offer_meta


logger = logging.getLogger(__name__)

TUTU_API_URL = "https://offers-api.tutu.ru/avia/offers"
TUTU_AUTOCOMPLETE_URL = "https://autocomplete-api.tutu.ru/v1/suggest"
TUTU_REFERER = "https://avia.tutu.ru/"
READ_TIMEOUT = 40.0
TUTU_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=READ_TIMEOUT,
    write=10.0,
    pool=10.0,
)
CITY_ID_TO_NAME = {value: name for name, value in CITY_MAPPING.items()}


async def get_city_id_from_api(city_name: str) -> Optional[int]:
    """Resolve a city through Tutu autocomplete."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        params = {"query": city_name, "lang": "ru"}

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                TUTU_AUTOCOMPLETE_URL,
                headers=headers,
                params=params,
            )

            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if items and len(items) > 0:
                    city_id = items[0].get("id")
                    city_real_name = items[0].get("name", city_name)
                    logging.info("Найден город '%s' с ID %s", city_real_name, city_id)
                    return city_id

        logging.warning("Город '%s' не найден через autocomplete API", city_name)
        return None
    except Exception as exc:
        logging.error("Ошибка получения CityId для '%s': %s", city_name, exc)
        return None


async def resolve_city_id(city_name: str) -> Optional[int]:
    """Resolve a city alias using the static map first, then autocomplete."""
    city_lower = city_name.lower().strip()
    if city_lower in CITY_MAPPING:
        return CITY_MAPPING[city_lower]

    logging.info("Город '%s' не найден в маппинге, запрашиваю через API...", city_name)
    return await get_city_id_from_api(city_name)


async def fetch_offers(
    origin_id: int,
    destination_id: int,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
) -> List[Dict]:
    """Fetch raw offers from Tutu's offers endpoint."""
    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://avia.tutu.ru",
            "Referer": TUTU_REFERER,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        routes = [
            {
                "departureCityId": origin_id,
                "arrivalCityId": destination_id,
                "departureDate": departure_date,
            }
        ]
        if return_date:
            routes.append(
                {
                    "departureCityId": destination_id,
                    "arrivalCityId": origin_id,
                    "departureDate": return_date,
                }
            )

        payload = {
            "passengers": {"full": passengers, "child": 0, "infant": 0},
            "serviceClass": "Y",
            "routes": routes,
            "pageId": "".join(chr(ord("a") + index % 26) for index in range(11)),
            "searchId": str(uuid.uuid4()),
            "sessionId": str(uuid.uuid4()),
            "source": "offers",
            "userData": {"screenSize": "md"},
        }

        logger.info(
            "Запрос: %s → %s, %s",
            CITY_ID_TO_NAME.get(origin_id, origin_id),
            CITY_ID_TO_NAME.get(destination_id, destination_id),
            departure_date,
        )
        logger.debug("Payload: %s", payload)
        start_time = datetime.now()

        async with httpx.AsyncClient(timeout=TUTU_TIMEOUT, http2=True) as client:
            try:
                response = await client.post(
                    TUTU_API_URL,
                    headers=headers,
                    json=payload,
                )
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info("HTTP %s, время: %.2fs", response.status_code, elapsed)

                if response.status_code != 200:
                    logger.error("Ошибка API: %s", response.status_code)
                    return []

                data = response.json()
                logger.debug("Тип ответа: %s", type(data))
                if isinstance(data, list) and len(data) > 0:
                    logger.debug("Ответ - список из %s элементов, берем первый", len(data))
                    data = data[0]
                if not isinstance(data, dict):
                    logger.error("Неожиданный тип ответа: %s", type(data))
                    return []

                logger.debug("Ключи верхнего уровня: %s", list(data.keys()))
                offers_dict = data.get("offers", {})
                logger.debug("Тип offers: %s", type(offers_dict))
                if not isinstance(offers_dict, dict):
                    logger.error("Неожиданная структура offers: %s", type(offers_dict))
                    return []

                logger.debug("Ключи offers: %s", list(offers_dict.keys()))
                actual = offers_dict.get("actual", {})
                logger.debug("Тип actual: %s", type(actual))
                if not isinstance(actual, dict):
                    logger.error("actual не является словарем: %s", type(actual))
                    return []

                logger.info("Количество офферов в actual: %s", len(actual))
                if not actual:
                    future = offers_dict.get("future")
                    past = offers_dict.get("past")
                    logger.warning(
                        "actual пустой. future: %s, past: %s",
                        type(future),
                        type(past),
                    )
                    warnings = data.get("warnings", [])
                    if warnings:
                        logger.warning("API warnings: %s", warnings)
                    return []

                dictionary = data.get("dictionary", {})
                offers_list = []
                for offer_id, offer_data in actual.items():
                    offer_data["id"] = offer_id
                    offer_data["_dictionary"] = dictionary
                    offers_list.append(offer_data)

                logger.info("Получено %s офферов", len(offers_list))
                return offers_list
            except httpx.ReadTimeout:
                logger.warning(
                    "Tutu API долго отвечает (> %ss), запрос пропущен",
                    READ_TIMEOUT,
                )
                return []
            except httpx.RequestError as exc:
                logger.error("Ошибка сети: %s", exc)
                return []
    except Exception as exc:
        logger.error("Ошибка в fetch_offers: %s", exc)
        return []


def parse_offer(offer: Dict) -> Optional[Dict]:
    """Parse one Tutu API offer into the bot's normalized ticket shape."""
    try:
        def format_duration(minutes: int) -> str:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}ч {mins}м" if mins else f"{hours}ч"

        if not isinstance(offer, dict):
            return None

        result = {
            "price": 0,
            "currency": "RUB",
            "airline": "Неизвестно",
            "departure": "",
            "arrival": "",
            "duration": "",
            "stops": 0,
            "baggage": False,
            "deeplink": "",
            "trips": [],
        }

        dictionary = offer.get("_dictionary", {})
        common_dict = dictionary.get("common", {})
        avia_dict = dictionary.get("avia", {})
        segments_dict = common_dict.get("segments", {})
        routes_dict = common_dict.get("routes", {})
        voyages_dict = avia_dict.get("voyages", {})

        offer_variants = offer.get("offerVariants")
        current_variant = {}
        if offer_variants:
            if isinstance(offer_variants, list) and len(offer_variants) > 0:
                current_variant = min(
                    offer_variants,
                    key=lambda item: item.get("price", {})
                    .get("value", {})
                    .get("amount", float("inf")),
                )
            elif isinstance(offer_variants, dict):
                current_variant = min(
                    offer_variants.values(),
                    key=lambda item: item.get("price", {})
                    .get("value", {})
                    .get("amount", float("inf")),
                )

        price_obj = current_variant.get("price") or offer.get("price", {})
        if isinstance(price_obj, (int, float)):
            result["price"] = int(price_obj)
        elif isinstance(price_obj, dict):
            value = price_obj.get("value")
            if isinstance(value, dict):
                amount = value.get("amount", 0)
                if value.get("fraction") == 100:
                    amount //= 100
                result["price"] = int(amount)
                result["currency"] = value.get("currencyCode", "RUB")
            elif "amount" in price_obj:
                result["price"] = int(price_obj["amount"])

        if result["price"] == 0:
            return None

        route_ids_raw = offer.get("routeIds") or current_variant.get("routeIds")
        if not route_ids_raw:
            route_ids_raw = offer.get("segmentIds") or current_variant.get("segmentIds")
        if not route_ids_raw:
            return None

        def collect_legs_for_route(route_id: str) -> List[Dict]:
            collected = []
            if route_id in routes_dict:
                route_obj = routes_dict[route_id]
                for segment_id in route_obj.get("segmentIds", []):
                    segment = segments_dict.get(segment_id)
                    if segment:
                        collected.append(segment)
            elif route_id in segments_dict:
                collected.append(segments_dict[route_id])
            elif route_id in voyages_dict:
                collected.append(voyages_dict[route_id])
            else:
                for part in route_id.split("/"):
                    if part in segments_dict:
                        collected.append(segments_dict[part])
                    elif part in voyages_dict:
                        collected.append(voyages_dict[part])
            return collected

        trips = []
        for route_id in route_ids_raw:
            if not isinstance(route_id, str):
                continue
            legs = collect_legs_for_route(route_id)
            if legs:
                trips.append(legs)
        if not trips:
            return None

        first_leg = trips[0][0]
        last_leg = trips[-1][-1]
        result["departure"] = (
            first_leg.get("departureDateTime")
            or first_leg.get("departureTime")
            or first_leg.get("departureDate")
            or first_leg.get("datetimeBeg", "")
        )
        result["arrival"] = (
            last_leg.get("arrivalDateTime")
            or last_leg.get("arrivalTime")
            or last_leg.get("arrivalDate")
            or last_leg.get("datetimeEnd", "")
        )

        total_duration = 0
        total_stops = 0
        for trip_legs in trips:
            trip_duration = sum(
                leg.get("durationMinutes", 0) or leg.get("duration", 0)
                for leg in trip_legs
            )
            total_duration += trip_duration
            total_stops += max(len(trip_legs) - 1, 0)
            trip_first_leg = trip_legs[0]
            trip_last_leg = trip_legs[-1]
            result["trips"].append(
                {
                    "departure": (
                        trip_first_leg.get("departureDateTime")
                        or trip_first_leg.get("departureTime")
                        or trip_first_leg.get("departureDate")
                        or trip_first_leg.get("datetimeBeg", "")
                    ),
                    "arrival": (
                        trip_last_leg.get("arrivalDateTime")
                        or trip_last_leg.get("arrivalTime")
                        or trip_last_leg.get("arrivalDate")
                        or trip_last_leg.get("datetimeEnd", "")
                    ),
                    "duration": format_duration(trip_duration),
                    "stops": max(len(trip_legs) - 1, 0),
                }
            )

        result["duration"] = format_duration(total_duration)
        result["stops"] = total_stops

        carrier_name = "Неизвестно"
        carrier_id = first_leg.get("carrier")
        if not carrier_id:
            carriers_list = first_leg.get("carriers", [])
            if carriers_list:
                carrier_id = carriers_list[0].get("id")
        if carrier_id:
            carriers_dict = common_dict.get("carriers", {})
            carrier = carriers_dict.get(str(carrier_id)) or carriers_dict.get(carrier_id)
            if carrier:
                carrier_name = carrier.get("name", "Неизвестно")
        result["airline"] = carrier_name

        fare_id = current_variant.get("fareApplicationId") or offer.get("fareApplicationId")
        if fare_id:
            conditions = avia_dict.get("conditions", {})
            fare = conditions.get(str(fare_id))
            if fare:
                baggage = fare.get("baggage", {})
                if isinstance(baggage, dict):
                    result["baggage"] = baggage.get("included", False) or (
                        baggage.get("weight", 0) > 0
                    )
                elif isinstance(baggage, bool):
                    result["baggage"] = baggage

        for trip in result["trips"]:
            trip["baggage"] = result["baggage"]
        return result
    except Exception:
        return None


async def search_tickets(
    origin_name: str,
    destination_name: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    requested_departure: Optional[str] = None,
    requested_return: Optional[str] = None,
) -> List[Dict]:
    """Run a complete Tutu search for one origin/destination pair."""
    origin_id = await resolve_city_id(origin_name)
    destination_id = await resolve_city_id(destination_name)
    if not origin_id or not destination_id:
        return []

    offers = await fetch_offers(
        origin_id,
        destination_id,
        departure_date,
        return_date,
        passengers,
    )
    if not offers:
        return []

    try:
        dep_dt = datetime.strptime(departure_date, "%Y-%m-%d")
        date_str = dep_dt.strftime("%d%m%Y")
        search_link = (
            "https://avia.tutu.ru/offers/?"
            f"passengers={passengers}&class=Y"
            f"&route[0]={origin_id}-{date_str}-{destination_id}"
            "&changes=all"
        )
        if return_date:
            ret_dt = datetime.strptime(return_date, "%Y-%m-%d")
            ret_str = ret_dt.strftime("%d%m%Y")
            search_link += f"&route[1]={destination_id}-{ret_str}-{origin_id}"
    except Exception as exc:
        logging.error("Ошибка ссылки: %s", exc)
        search_link = "https://avia.tutu.ru/"

    logging.info("Начинаю парсинг %s офферов...", len(offers))
    tickets = []
    for offer in offers:
        ticket = parse_offer(offer)
        if ticket and ticket["price"] > 0:
            ticket["deeplink"] = search_link
            ticket["meta"] = build_offer_meta(
                departure_date,
                return_date,
                requested_departure,
                requested_return,
            )
            tickets.append(ticket)

    tickets.sort(key=lambda item: item["price"])
    return tickets[:7]


async def multi_destination_search(
    origins: List[Dict],
    destinations: List[Dict],
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    requested_departure: Optional[str] = None,
    requested_return: Optional[str] = None,
) -> List[Dict]:
    """Search all origin/destination pairs and annotate the normalized tickets."""
    all_tickets = []
    for origin in origins:
        for destination in destinations:
            tickets = await search_tickets(
                origin["name"],
                destination["name"],
                departure_date,
                return_date,
                passengers,
                requested_departure,
                requested_return,
            )
            for ticket in tickets:
                ticket["origin_name"] = origin["name"]
                ticket["destination_name"] = destination["name"]
            all_tickets.extend(tickets)
            await asyncio.sleep(2)
    return all_tickets
