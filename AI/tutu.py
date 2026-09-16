# tutu.py

import asyncio
import logging
import re
import uuid
from datetime import date, datetime
from typing import Dict, List, Optional

import httpx
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
from core.settings import ADMIN_ID
from infrastructure.ai.clients import groq_ai


logger = logging.getLogger(__name__)

# Keep the historical public helper surface while the implementation lives in
# the dedicated parsing/date module.
_PARSING_REEXPORTS = (
    MAX_DATE_VARIANTS,
    parse_date,
    parse_date_range,
)

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

# Обратный маппинг для форматирования
CITY_ID_TO_NAME = {v: k for k, v in CITY_MAPPING.items()}


async def get_city_id_from_api(city_name: str) -> Optional[int]:
    """
    Получает CityId через Tutu autocomplete API.

    Используется как fallback, если города нет в CITY_MAPPING.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        params = {
            "query": city_name,
            "lang": "ru",
        }

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
    """
    Резолвит название города в CityId.

    1. Сначала проверяет CITY_MAPPING
    2. Если не найдено - запрашивает через autocomplete API
    """
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
    """
    Получает предложения с Tutu API.

    КРИТИЧНО: единственный источник данных - POST /avia/offers

    Returns: список офферов или []
    """
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

        session_id = str(uuid.uuid4())
        search_id = str(uuid.uuid4())
        page_id = "".join(chr(ord("a") + i % 26) for i in range(11))

        payload = {
            "passengers": {
                "full": passengers,
                "child": 0,
                "infant": 0,
            },
            "serviceClass": "Y",
            "routes": routes,
            "pageId": page_id,
            "searchId": search_id,
            "sessionId": session_id,
            "source": "offers",
            "userData": {
                "screenSize": "md",
            },
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

                if isinstance(offers_dict, dict):
                    logger.debug("Ключи offers: %s", list(offers_dict.keys()))

                    actual = offers_dict.get("actual", {})
                    logger.debug("Тип actual: %s", type(actual))

                    if isinstance(actual, dict):
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

                        offers = actual
                    else:
                        logger.error("actual не является словарем: %s", type(actual))
                        return []
                else:
                    logger.error("Неожиданная структура offers: %s", type(offers_dict))
                    return []

                offers_list = []
                if isinstance(offers, dict):
                    dictionary = data.get("dictionary", {})
                    for offer_id, offer_data in offers.items():
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
    """
    Парсит оффер Tutu (API 2026).
    v7.0: Чистый парсинг данных. Ссылка формируется в search_tickets.
    """
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
                seg_ids = route_obj.get("segmentIds", [])
                for seg_id in seg_ids:
                    segment = segments_dict.get(seg_id)
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
            trips_info = {
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
            result["trips"].append(trips_info)

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
    """Полный цикл поиска одного направления."""
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
    """Поиск билетов по множественным направлениям."""
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
