"""Daily holiday digest from calend.ru."""

import asyncio
import html
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pytz
import requests
from aiogram import Bot
from aiogram import types
from bs4 import BeautifulSoup

from prompts import PROMPTS_MEDIA

CALEND_BASE_URL = "https://www.calend.ru"
HOLIDAYS_CHAT_ID = -1001707530786
MAX_HOLIDAYS = 5


@dataclass(frozen=True)
class Holiday:
    title: str
    category: str
    description: str
    url: str


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _fetch_calend_holidays_sync(month: int, day: int) -> list[Holiday]:
    url = f"{CALEND_BASE_URL}/holidays/{month}-{day}/"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    holidays: list[Holiday] = []

    for card in soup.select(".caption"):
        title_link = card.select_one(".title a")
        description_node = card.select_one("p.descr")
        if not title_link or not description_node:
            continue

        title = _normalize_text(title_link.get_text(" ", strip=True))
        description = _normalize_text(description_node.get_text(" ", strip=True))
        category = _normalize_text(card.select_one(".link").get_text(" ", strip=True) if card.select_one(".link") else "")
        if not title or not description:
            continue

        holidays.append(
            Holiday(
                title=title,
                category=category,
                description=description,
                url=urljoin(CALEND_BASE_URL, title_link.get("href", "")),
            )
        )

    return holidays[:MAX_HOLIDAYS]


async def fetch_today_holidays() -> list[Holiday]:
    moscow_tz = pytz.timezone("Europe/Moscow")
    now = datetime.now().astimezone(moscow_tz)
    return await asyncio.to_thread(_fetch_calend_holidays_sync, now.month, now.day)


def _extract_json_list(text: str) -> list[dict]:
    json_match = re.search(r"```json\s*(.*?)\s*```", text or "", re.DOTALL | re.IGNORECASE)
    json_text = json_match.group(1) if json_match else (text or "").strip()
    if not json_text.startswith("["):
        bracket_match = re.search(r"(\[.*\])", json_text, re.DOTALL)
        if bracket_match:
            json_text = bracket_match.group(1)
    parsed = json.loads(json_text)
    return parsed if isinstance(parsed, list) else []


def _build_digest_prompt(holidays: list[Holiday]) -> str:
    source = "\n\n".join(
        f"{index}. {holiday.title}\n"
        f"Категория: {holiday.category or 'не указана'}\n"
        f"Описание с calend.ru: {holiday.description}"
        for index, holiday in enumerate(holidays, 1)
    )
    media_prompt = random.choice(PROMPTS_MEDIA)
    return (
        f"{media_prompt}\n\n"
        "Сделай ежедневную рассылку праздников для Telegram по данным ниже. "
        "Для каждого праздника верни короткое описание на русском: 1-3 предложения, без вводной болтовни. "
        "Сохрани ровно эти названия праздников, не добавляй праздники от себя. "
        "Ответ верни строго JSON-массивом без markdown: "
        '[{"title": "название", "description": "краткое описание"}].\n\n'
        f"Данные:\n{source}"
    )


async def generate_holiday_descriptions(holidays: list[Holiday], chat_id: int) -> dict[str, str]:
    from AI.talking import generate_simple_response

    if not holidays:
        return {}

    try:
        prompt = _build_digest_prompt(holidays)
        response_text = await generate_simple_response(prompt, str(chat_id))
        generated = _extract_json_list(response_text)
    except Exception as e:
        logging.warning(f"Holiday digest generation failed: {e}", exc_info=True)
        return {}

    descriptions: dict[str, str] = {}
    known_titles = {holiday.title for holiday in holidays}
    for item in generated:
        if not isinstance(item, dict):
            continue
        title = _normalize_text(str(item.get("title", "")))
        description = _normalize_text(str(item.get("description", "")))
        if title in known_titles and description:
            descriptions[title] = description
    return descriptions


def _fallback_description(text: str, limit: int = 450) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def format_holiday_digest(holidays: list[Holiday], descriptions: dict[str, str] | None = None) -> str:
    descriptions = descriptions or {}
    lines = ["<b>Праздники:</b>"]

    for holiday in holidays:
        title = html.escape(holiday.title)
        url = html.escape(holiday.url, quote=True)
        description = html.escape(descriptions.get(holiday.title) or _fallback_description(holiday.description))
        lines.append(f'\n🎉 <a href="{url}">{title}</a>\n{description}')

    return "\n".join(lines)


async def send_daily_holidays(bot: Bot, chat_id: int = HOLIDAYS_CHAT_ID) -> None:
    holidays = await fetch_today_holidays()
    if not holidays:
        logging.warning("Holiday digest: calend.ru returned no holidays")
        return

    descriptions = await generate_holiday_descriptions(holidays, chat_id)
    await bot.send_message(
        chat_id,
        format_holiday_digest(holidays, descriptions),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def process_holidays_command(message: types.Message) -> None:
    holidays = await fetch_today_holidays()
    if not holidays:
        await message.reply("Праздники не загрузились. calend.ru, видимо, решил отдохнуть.")
        return

    descriptions = await generate_holiday_descriptions(holidays, message.chat.id)
    await message.reply(
        format_holiday_digest(holidays, descriptions),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def schedule_daily_holidays(bot: Bot, chat_id: int = HOLIDAYS_CHAT_ID) -> None:
    while True:
        moscow_tz = pytz.timezone("Europe/Moscow")
        now = datetime.now().astimezone(moscow_tz)

        target_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            await send_daily_holidays(bot, chat_id)
        except Exception as e:
            logging.error(f"Holiday digest scheduler failed for chat {chat_id}: {e}", exc_info=True)
