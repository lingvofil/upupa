import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from aiogram import types

from config import groq_ai

SESSION_URLS = [
    "https://avia.tutu.ru/session",
    "https://avia.tutu.ru/api/session",
    "https://avia.tutu.ru/api/avia/session",
]
OFFERS_URL = "https://offers-api.tutu.ru/avia/offers"
SUGGESTS_URL = "https://avia.tutu.ru/api/suggests/"

DEFAULT_ORIGIN_NAME = "Москва"
DEFAULT_ORIGIN_IATA = "MOW"
DEFAULT_ADULTS = 1
DEFAULT_CLASS = "economy"

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

CITY_MAPPING = {
    "москва": {"code": "MOW", "name": "Москва"},
    "мск": {"code": "MOW", "name": "Москва"},
    "санкт-петербург": {"code": "LED", "name": "Санкт-Петербург"},
    "спб": {"code": "LED", "name": "Санкт-Петербург"},
    "питер": {"code": "LED", "name": "Санкт-Петербург"},
    "сочи": {"code": "AER", "name": "Сочи"},
    "казань": {"code": "KZN", "name": "Казань"},
    "екатеринбург": {"code": "SVX", "name": "Екатеринбург"},
    "новосибирск": {"code": "OVB", "name": "Новосибирск"},
    "дубай": {"code": "DXB", "name": "Дубай"},
    "пхукет": {"code": "HKT", "name": "Пхукет"},
    "стамбул": {"code": "IST", "name": "Стамбул"},
    "париж": {"code": "PAR", "name": "Париж"},
    "рим": {"code": "ROM", "name": "Рим"},
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://avia.tutu.ru/",
    "Origin": "https://avia.tutu.ru",
}


@dataclass
class CityInfo:
    code: str
    name: str


@dataclass
class SearchParams:
    origin: CityInfo
    destinations: List[CityInfo]
    depart_date: str
    return_date: Optional[str]
    adults: int
    month_range: Optional[Tuple[str, str]]
    raw_text: str


@dataclass
class OfferSummary:
    price: int
    currency: str
    airline: str
    departure: str
    arrival: str
    duration_minutes: int
    stops: int
    layovers: List[str]
    baggage: str
    carry_on: str
    is_multi_pnr: bool
    link: str
    score: float
    ai_comment: Optional[str] = None
    tag: Optional[str] = None


def _parse_date_token(token: str) -> Optional[datetime]:
    match = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$", token)
    if not match:
        return None
    day, month, year = match.groups()
    year_int = None
    if year:
        year_int = int(year) if len(year) == 4 else 2000 + int(year)
    else:
        year_int = datetime.now().year
    try:
        candidate = datetime(year_int, int(month), int(day))
    except ValueError:
        return None
    if candidate.date() < datetime.now().date():
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError:
            return None
    return candidate


def _parse_date_range(text: str) -> Optional[Tuple[str, str]]:
    match = re.search(r"(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s*-\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)", text)
    if not match:
        return None
    start_token, end_token = match.groups()
    start_date = _parse_date_token(start_token)
    end_date = _parse_date_token(end_token)
    if not start_date or not end_date:
        return None
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def _get_month_range(month: int) -> Tuple[str, str]:
    year = datetime.now().year
    first_day = datetime(year, month, 1)
    if first_day.date() < datetime.now().date():
        first_day = datetime(year + 1, month, 1)
    next_month = first_day.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    return first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")


def _normalize_city_token(token: str) -> str:
    return token.strip().lower().replace("ё", "е")


def _extract_month(tokens: List[str]) -> Optional[int]:
    for token in tokens:
        normalized = _normalize_city_token(token)
        if normalized in MONTH_MAPPING:
            return MONTH_MAPPING[normalized]
    return None


def _extract_cities(tokens: List[str]) -> List[str]:
    cleaned = []
    for token in tokens:
        if token:
            cleaned.append(_normalize_city_token(token))
    return cleaned


def _format_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return date_str


def _format_duration(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}ч {mins}м" if mins else f"{hours}ч"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _merge_dictionary(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = _merge_dictionary(target[key], value)
        else:
            target[key] = value
    return target


def parse_search_command(text: str) -> Optional[SearchParams]:
    if not text:
        return None
    text_lower = text.strip().lower()
    if not text_lower.startswith("билеты"):
        return None

    raw_args = text_lower[len("билеты"):].strip()
    tokens = re.split(r"\s+", raw_args) if raw_args else []

    date_range = _parse_date_range(raw_args)
    raw_args_without_range = raw_args
    if date_range:
        raw_args_without_range = re.sub(r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\s*-\s*\d{1,2}\.\d{1,2}(?:\.\d{2,4})?", "", raw_args)

    tokens = re.split(r"\s+", raw_args_without_range.strip()) if raw_args_without_range.strip() else []
    month = _extract_month(tokens)

    date_token = None
    for token in tokens:
        parsed = _parse_date_token(token)
        if parsed:
            date_token = parsed
            break

    city_tokens = []
    for token in tokens:
        if _parse_date_token(token):
            continue
        if _normalize_city_token(token) in MONTH_MAPPING:
            continue
        city_tokens.append(token)

    city_names = _extract_cities(city_tokens)

    origin = CityInfo(DEFAULT_ORIGIN_IATA, DEFAULT_ORIGIN_NAME)
    destinations: List[CityInfo] = []

    if len(city_names) == 1:
        destinations = [CityInfo(code=city_names[0].upper(), name=city_names[0].title())]
    elif len(city_names) == 2:
        origin = CityInfo(code=city_names[0].upper(), name=city_names[0].title())
        destinations = [CityInfo(code=city_names[1].upper(), name=city_names[1].title())]
    elif len(city_names) > 2:
        destinations = [CityInfo(code=city.upper(), name=city.title()) for city in city_names]

    depart_date = None
    return_date = None
    month_range = None

    if date_range:
        depart_date, return_date = date_range
    elif date_token:
        depart_date = date_token.strftime("%Y-%m-%d")
    elif month:
        month_range = _get_month_range(month)
        depart_date = month_range[0]
    else:
        depart_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    return SearchParams(
        origin=origin,
        destinations=destinations,
        depart_date=depart_date,
        return_date=return_date,
        adults=DEFAULT_ADULTS,
        month_range=month_range,
        raw_text=text,
    )


async def resolve_city(city: CityInfo, client: httpx.AsyncClient) -> CityInfo:
    normalized = _normalize_city_token(city.name)
    if normalized in CITY_MAPPING:
        mapped = CITY_MAPPING[normalized]
        return CityInfo(code=mapped["code"], name=mapped["name"])

    params = {"term": city.name, "source": "avia", "limit": 5}
    try:
        response = await client.get(SUGGESTS_URL, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            first = data[0]
            code = first.get("code") or first.get("iata")
            name = first.get("name") or first.get("title")
            if code and name:
                return CityInfo(code=code, name=name)
    except Exception as exc:
        logging.warning("Не удалось получить IATA для %s: %s", city.name, exc)

    if len(city.code) == 3 and city.code.isalpha():
        return CityInfo(code=city.code.upper(), name=city.name.title())

    return city


def build_session_payload(params: SearchParams, destination: CityInfo) -> Dict[str, Any]:
    segments = [
        {
            "origin": params.origin.code,
            "destination": destination.code,
            "date": params.depart_date,
        }
    ]
    if params.return_date:
        segments.append(
            {
                "origin": destination.code,
                "destination": params.origin.code,
                "date": params.return_date,
            }
        )

    payload = {
        "segments": segments,
        "passengers": {"adults": params.adults, "children": 0, "infants": 0},
        "class": DEFAULT_CLASS,
    }

    if params.month_range:
        payload["departureDateRange"] = {
            "from": params.month_range[0],
            "to": params.month_range[1],
        }

    return payload


async def create_search_session(client: httpx.AsyncClient, payload: Dict[str, Any]) -> Optional[str]:
    last_error: Optional[Exception] = None
    for url in SESSION_URLS:
        try:
            response = await client.post(url, json=payload, timeout=15.0)
            response.raise_for_status()
            data = response.json()
            session_id = data.get("sessionId") or data.get("session_id")
            if session_id:
                return session_id
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in {404, 405}:
                raise
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return None


def _parse_streaming_chunks(text: str) -> List[Dict[str, Any]]:
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not chunks:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                chunks = [payload]
        except json.JSONDecodeError:
            pass
    return chunks


async def poll_offers(
    client: httpx.AsyncClient,
    session_id: str,
    max_attempts: int = 12,
    delay: float = 1.5,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    dictionary: Dict[str, Any] = {}
    offers: List[Dict[str, Any]] = []
    completed = False

    for _ in range(max_attempts):
        response = await client.get(OFFERS_URL, params={"sessionId": session_id}, timeout=20.0)
        response.raise_for_status()
        chunks = _parse_streaming_chunks(response.text)
        for chunk in chunks:
            if chunk.get("dictionary"):
                dictionary = _merge_dictionary(dictionary, chunk.get("dictionary", {}))
            if chunk.get("offers"):
                offers.extend(chunk.get("offers", []))
            if chunk.get("search_completed") or chunk.get("searchCompleted"):
                completed = True
        if completed:
            break
        await asyncio.sleep(delay)

    return dictionary, offers, completed


def _lookup(dictionary: Dict[str, Any], path: List[str], key: Any) -> Optional[Dict[str, Any]]:
    current = dictionary
    for part in path:
        current = current.get(part, {}) if isinstance(current, dict) else {}
    if isinstance(current, dict):
        return current.get(str(key)) or current.get(key)
    if isinstance(current, list):
        for item in current:
            if item.get("id") == key:
                return item
    return None


def _parse_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_offer_summary(
    offer: Dict[str, Any],
    dictionary: Dict[str, Any],
    origin: CityInfo,
    destination: CityInfo,
) -> Optional[OfferSummary]:
    price_info = offer.get("price", {})
    price = _safe_int(price_info.get("amount") or price_info.get("value"))
    currency = price_info.get("currency", "RUB")
    if currency != "RUB":
        return None

    segment_ids = offer.get("segmentIds") or offer.get("segments") or []
    segments = []
    for seg_id in segment_ids:
        segment = _lookup(dictionary, ["avia", "segments"], seg_id)
        if segment:
            segments.append(segment)

    if not segments:
        return None

    departure_time = _parse_time(segments[0].get("departure"))
    arrival_time = _parse_time(segments[-1].get("arrival"))

    duration_minutes = 0
    layovers: List[str] = []
    stops = max(len(segments) - 1, 0)

    for idx, segment in enumerate(segments):
        duration_minutes += _safe_int(segment.get("duration"), 0)
        if idx < len(segments) - 1:
            next_seg = segments[idx + 1]
            arrival = _parse_time(segment.get("arrival"))
            next_depart = _parse_time(next_seg.get("departure"))
            if arrival and next_depart:
                layover_minutes = int((next_depart - arrival).total_seconds() / 60)
                airport_id = next_seg.get("origin")
                airport = _lookup(dictionary, ["common", "airports"], airport_id) or {}
                city_id = airport.get("cityId")
                city = _lookup(dictionary, ["common", "cities"], city_id) or {}
                city_name = city.get("name") or "Пересадка"
                layovers.append(f"{city_name}, {_format_duration(max(layover_minutes, 0))}")

    if not duration_minutes and departure_time and arrival_time:
        duration_minutes = int((arrival_time - departure_time).total_seconds() / 60)

    carrier_id = segments[0].get("marketingCarrierId")
    carrier = _lookup(dictionary, ["common", "carriers"], carrier_id) or {}
    airline = carrier.get("name", "Авиакомпания")

    operating_id = segments[0].get("operatingCarrierId")
    if operating_id and operating_id != carrier_id:
        operating = _lookup(dictionary, ["common", "carriers"], operating_id) or {}
        operating_name = operating.get("name")
        if operating_name:
            airline = f"{airline} (рейс {operating_name})"

    baggage = "Без багажа"
    carry_on = "Ручная кладь"
    fare_ids = offer.get("fareApplications") or offer.get("fareApplicationIds") or []
    if fare_ids:
        fare = _lookup(dictionary, ["avia", "fareApplications"], fare_ids[0]) or {}
        condition_id = fare.get("conditionId")
        condition = _lookup(dictionary, ["avia", "conditions"], condition_id) or {}
        baggage_info = condition.get("baggage") or {}
        carry_info = condition.get("carryOn") or {}
        baggage = baggage_info.get("text") or baggage_info.get("weight") or baggage
        carry_on = carry_info.get("text") or carry_info.get("weight") or carry_on

    is_multi_pnr = bool(offer.get("isMultiPnr") or offer.get("is_multi_pnr"))
    link = offer.get("deeplink") or offer.get("link") or offer.get("url") or "https://avia.tutu.ru"

    score = price * 0.5 + duration_minutes * 0.3 + stops * 0.2

    return OfferSummary(
        price=price,
        currency=currency,
        airline=airline,
        departure=departure_time.strftime("%H:%M") if departure_time else "",
        arrival=arrival_time.strftime("%H:%M") if arrival_time else "",
        duration_minutes=duration_minutes,
        stops=stops,
        layovers=layovers,
        baggage=str(baggage),
        carry_on=str(carry_on),
        is_multi_pnr=is_multi_pnr,
        link=link,
        score=score,
    )


def pick_top_offers(offers: List[OfferSummary]) -> List[OfferSummary]:
    if not offers:
        return []

    cheapest = min(offers, key=lambda o: o.price)
    fastest = min(offers, key=lambda o: o.duration_minutes)
    optimal = min(offers, key=lambda o: o.score)

    picked = []
    for offer, tag in [
        (cheapest, "🔥 Самый дешевый"),
        (fastest, "⚡ Самый быстрый"),
        (optimal, "⭐ Оптимальный"),
    ]:
        if offer not in picked:
            offer.tag = tag
            picked.append(offer)
    return picked


def build_ai_prompt(offer: OfferSummary, origin: CityInfo, destination: CityInfo) -> str:
    return (
        "Ты — тревел-ассистент. Сформулируй краткий комментарий (1-2 предложения) "
        "по авиабилету. Укажи преимущества/риски. Ответ на русском."
        f"\nМаршрут: {origin.name} → {destination.name}"
        f"\nЦена: {offer.price} ₽"
        f"\nАвиакомпания: {offer.airline}"
        f"\nДлительность: {_format_duration(offer.duration_minutes)}"
        f"\nПересадки: {offer.stops} ({', '.join(offer.layovers) if offer.layovers else 'без пересадок'})"
        f"\nБагаж: {offer.baggage}, ручная кладь: {offer.carry_on}"
        f"\nРаздельные билеты: {'да' if offer.is_multi_pnr else 'нет'}"
    )


def generate_ai_comment(offer: OfferSummary, origin: CityInfo, destination: CityInfo) -> Optional[str]:
    if not groq_ai:
        return None
    prompt = build_ai_prompt(offer, origin, destination)
    try:
        response = groq_ai.generate_text(prompt)
        if response:
            return response.strip()
    except Exception as exc:
        logging.warning("AI комментарий не получен: %s", exc)
    return None


def format_offer_block(offer: OfferSummary, index: int) -> str:
    layover_text = "Прямой рейс" if offer.stops == 0 else ", ".join(offer.layovers)
    baggage_text = offer.baggage
    if "без" in offer.baggage.lower():
        baggage_text = "🚫 Без багажа"
    else:
        baggage_text = f"🧳 {offer.baggage}"

    warning = "\n⚠️ <i>Раздельные билеты (MultiPNR)</i>" if offer.is_multi_pnr else ""

    ai_comment = f"\n🤖 <i>{offer.ai_comment}</i>" if offer.ai_comment else ""

    return (
        f"{index}. <b>{offer.airline}</b> ({offer.tag})\n"
        f"💰 <b>{offer.price:,} ₽</b> | {baggage_text}\n"
        f"🕒 {_format_duration(offer.duration_minutes)} "
        f"({offer.stops} пересадка(и): {layover_text})"
        f"{ai_comment}"
        f"{warning}\n"
        f"<a href=\"{offer.link}\">Купить билет</a>"
    )


def format_message(
    origin: CityInfo,
    destination: CityInfo,
    params: SearchParams,
    offers: List[OfferSummary],
    total_found: int,
) -> str:
    date_info = _format_date(params.depart_date)
    if params.return_date:
        date_info = f"{date_info} — {_format_date(params.return_date)}"
    elif params.month_range:
        start, end = params.month_range
        date_info = f"{_format_date(start)} — {_format_date(end)}"

    header = (
        f"✈️ <b>Билеты: {origin.name} → {destination.name}</b>\n"
        f"📅 {date_info} | 👤 {params.adults} чел.\n\n"
    )

    if not offers:
        return header + "😢 Подходящих билетов не найдено. Попробуйте другие даты или направление."

    blocks = []
    for idx, offer in enumerate(offers, start=1):
        blocks.append(format_offer_block(offer, idx))

    footer = f"\n------------------------------\n⚠️ <i>Найдено вариантов: {total_found}. Показаны лучшие.</i>"
    return header + "\n------------------------------\n" + "\n\n------------------------------\n".join(blocks) + footer


async def search_tickets(params: SearchParams) -> List[str]:
    messages = []
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, http2=True, follow_redirects=True) as client:
        params.origin = await resolve_city(params.origin, client)
        destinations = [await resolve_city(dest, client) for dest in params.destinations]

        for destination in destinations:
            payload = build_session_payload(params, destination)
            try:
                session_id = await create_search_session(client, payload)
            except Exception as exc:
                logging.error("Ошибка создания сессии Tutu: %s", exc)
                messages.append(
                    f"❌ Не удалось создать сессию поиска для {destination.name}. Попробуйте позже."
                )
                continue

            if not session_id:
                messages.append(
                    f"❌ Не удалось получить sessionId для {destination.name}. Попробуйте позже."
                )
                continue

            try:
                dictionary, offers_raw, completed = await poll_offers(client, session_id)
            except Exception as exc:
                logging.error("Ошибка получения офферов Tutu: %s", exc)
                messages.append(
                    f"❌ Не удалось получить предложения для {destination.name}. Попробуйте позже."
                )
                continue

            offers = []
            for offer in offers_raw:
                summary = build_offer_summary(offer, dictionary, params.origin, destination)
                if summary:
                    offers.append(summary)

            offers.sort(key=lambda o: o.score)
            top_offers = pick_top_offers(offers)

            for offer in top_offers:
                offer.ai_comment = generate_ai_comment(offer, params.origin, destination)

            total_found = len(offers)
            if not completed:
                logging.info("Поиск еще не завершен, но продолжаем с текущими данными.")

            messages.append(format_message(params.origin, destination, params, top_offers, total_found))

    return messages


async def process_tutu_command(message: types.Message) -> None:
    params = parse_search_command(message.text or "")
    if not params:
        return

    if not params.destinations:
        await message.reply("Укажите направление: например, <b>билеты Сочи</b>.", parse_mode="HTML")
        return

    status_message = await message.reply("Ищу билеты, подождите... ⏳")
    try:
        messages = await search_tickets(params)
        await status_message.delete()

        for msg in messages:
            await message.reply(msg, parse_mode="HTML", disable_web_page_preview=True)
            await asyncio.sleep(1)
    except Exception as exc:
        logging.error("Ошибка в process_tutu_command: %s", exc, exc_info=True)
        await status_message.edit_text("❌ Произошла ошибка при поиске билетов. Попробуйте позже.")
