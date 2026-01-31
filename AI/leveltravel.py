import asyncio
import logging
import re
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import aiohttp
from aiogram import types

from config import groq_ai, ADMIN_ID

# =============================================================================
# НАСТРОЙКИ LEVEL.TRAVEL
# =============================================================================

API_BASE = "https://api.level.travel"
PUBLIC_KEY = "0fe9fb2ff35679322db5429b18a53aee"

DEPARTURE_ID_MOSCOW = 213
DEFAULT_DEPARTURE_CITY = "Moscow-RU"

# =============================================================================
# МЕСЯЦЫ
# =============================================================================

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

# =============================================================================
# НАПРАВЛЕНИЯ (ТОЛЬКО НУЖНЫЕ)
# place_id — КЛЮЧЕВОЙ параметр
# =============================================================================

DESTINATIONS = {
    "северный гоа": {"country": "IN", "place_id": 10145},
    "гоа": {"country": "IN", "place_id": 10145},
    "мальдивы": {"country": "MV", "place_id": 10038},
    "шри-ланка": {"country": "LK", "place_id": 10109},
    "шриланка": {"country": "LK", "place_id": 10109},
    "вьетнам": {"country": "VN", "place_id": 10053},
    "фукуок": {"country": "VN", "place_id": 10234},
    "нячанг": {"country": "VN", "place_id": 10163},
    "турция": {"country": "TR", "place_id": 10006},
    "бали": {"country": "ID", "place_id": 10112},
}

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def generate_dates_for_month(month: int) -> List[str]:
    today = datetime.now()
    year = today.year if month >= today.month else today.year + 1

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year, 12, 31)
    else:
        end = datetime(year, month + 1, 1) - timedelta(days=1)

    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=3)  # шаг 3 дня — оптимум

    return dates


def parse_tour_command(text: str) -> Dict:
    text = text.lower()

    params = {
        "month": None,
        "place_id": None,
        "country_name": None,
        "adults": 2,
        "nights": 8,
    }

    for k, v in MONTH_MAPPING.items():
        if k in text:
            params["month"] = v
            break

    for name, meta in DESTINATIONS.items():
        if name in text:
            params["place_id"] = meta["place_id"]
            params["country_name"] = name
            break

    nums = re.findall(r"\b([1-9])\b", text)
    if nums:
        params["adults"] = int(nums[0])

    nights = re.search(r"(\d+)\s*(ночей|ночи|ночь)", text)
    if nights:
        params["nights"] = int(nights.group(1))

    return params

# =============================================================================
# API LEVEL.TRAVEL
# =============================================================================

async def create_search_request(session, start_date, place_id, adults):
    params = {
        "start_date": start_date,
        "place_id": place_id,
        "departure_id": DEPARTURE_ID_MOSCOW,
        "adults": adults,
        "search_type": "package",
        "api_version": "3.7",
        "js": "true",
        "key": PUBLIC_KEY,
    }

    async with session.get(f"{API_BASE}/references/search_calendar_duration", params=params) as r:
        data = await r.json()
        return data.get("request_id")


async def wait_search_ready(session, request_id):
    for _ in range(25):
        async with session.get(f"{API_BASE}/search/status", params={
            "request_id": request_id,
            "api_version": "3.13",
            "js": "true",
            "key": PUBLIC_KEY,
        }) as r:
            data = await r.json()
            if data.get("status") == "done":
                return True
        await asyncio.sleep(1)
    return False


async def fetch_grouped_hotels(session, request_id):
    async with session.get(f"{API_BASE}/search/get_grouped_hotels", params={
        "request_id": request_id,
        "page_limit": 100,
        "page_number": 0,
        "sort_by": "relevance",
        "api_version": "3.13",
        "js": "true",
        "key": PUBLIC_KEY,
    }) as r:
        return await r.json()

# =============================================================================
# НОРМАЛИЗАЦИЯ
# =============================================================================

def normalize_hotels(data) -> List[Dict]:
    results = []

    for h in data.get("hotels", []):
        results.append({
            "hotel_name": h.get("name"),
            "price": h.get("min_price"),
            "rating": h.get("rating", 0),
            "reviews_count": h.get("reviews_count", 0),
            "stars": h.get("stars", 0),
            "nights": h.get("nights"),
            "url": "https://level.travel" + h.get("url", ""),
            "meal_type": h.get("meal_type"),
            "location": h.get("resort_name"),
        })

    return results

# =============================================================================
# GROQ АНАЛИЗ
# =============================================================================

async def analyze_with_groq(tours: List[Dict], country_name: str) -> List[Dict]:
    prompt = f"""
Ты опытный travel-эксперт.
Выбери ТОП-10 туров для направления "{country_name}".

КРИТЕРИИ:
- хорошие отзывы
- кондиционер
- купание в море
- не скучно
- адекватная цена

ДАННЫЕ:
{json.dumps(tours[:30], ensure_ascii=False, indent=2)}

ОТВЕТ ТОЛЬКО JSON:
[
  {{"index": 0, "score": 9, "reason": "кратко"}},
  ...
]
"""

    response = await groq_ai.generate_text(prompt, temperature=0.3)
    match = re.search(r"\[[\s\S]*\]", response)
    if not match:
        return tours[:10]

    analysis = json.loads(match.group())
    analysis.sort(key=lambda x: x["score"], reverse=True)

    result = []
    for item in analysis[:10]:
        idx = item["index"]
        t = tours[idx].copy()
        t["ai_score"] = item["score"]
        t["ai_reason"] = item["reason"]
        result.append(t)

    return result

# =============================================================================
# ХЭНДЛЕР КОМАНДЫ
# =============================================================================

async def process_tours_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    params = parse_tour_command(message.text)
    if not params["place_id"]:
        await message.reply("❌ Не указано направление")
        return

    dates = generate_dates_for_month(params["month"]) if params["month"] else []
    all_tours = []

    await message.reply("🔍 Ищу туры, подожди…")

    async with aiohttp.ClientSession() as session:
        for d in dates:
            request_id = await create_search_request(
                session,
                start_date=d,
                place_id=params["place_id"],
                adults=params["adults"],
            )
            if not request_id:
                continue

            ready = await wait_search_ready(session, request_id)
            if not ready:
                continue

            data = await fetch_grouped_hotels(session, request_id)
            all_tours.extend(normalize_hotels(data))

            if len(all_tours) >= 40:
                break

    if not all_tours:
        await message.reply("😕 Туры не найдены")
        return

    best = await analyze_with_groq(all_tours, params["country_name"])

    lines = [f"🏖 <b>Топ туров: {params['country_name'].title()}</b>\n"]
    for i, t in enumerate(best, 1):
        lines.append(
            f"<b>{i}. {t['hotel_name']}</b>\n"
            f"💰 {t['price']:,} ₽ | ⭐ {t['stars']} | 📊 {t['rating']}\n"
            f"💡 {t.get('ai_reason','')}\n"
            f"🔗 <a href='{t['url']}'>Подробнее</a>\n"
        )

    await message.reply("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
