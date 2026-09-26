"""Daily holiday digest from calend.ru."""

import asyncio
import html
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pytz
import requests
from aiogram import Bot
from aiogram import types
from bs4 import BeautifulSoup

from AI.dialog.generation import generate_simple_response
from AI.dialog.settings import build_prompt_with_current_chat_prompt
from core.state import chat_settings
from infrastructure.ai.execution import ai_feature

CALEND_BASE_URL = "https://www.calend.ru"
MAX_HOLIDAYS = 5
HOLIDAY_AI_ATTEMPTS = 2


@dataclass(frozen=True)
class Holiday:
    title: str
    category: str
    description: str
    url: str


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _normalize_title_key(text: str) -> str:
    """Normalize a generated holiday title for tolerant source matching."""
    normalized = unicodedata.normalize("NFKC", _normalize_text(text)).casefold().replace("ё", "е")
    return " ".join(
        "".join(char if char.isalnum() else " " for char in normalized).split()
    )


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


def get_holiday_broadcast_chat_ids() -> list[int]:
    """Возвращает чаты, где ежедневная рассылка праздников явно включена."""
    enabled_chat_ids: list[int] = []
    for chat_id, settings in chat_settings.items():
        if not isinstance(settings, dict) or not settings.get("holidays_enabled", False):
            continue
        try:
            enabled_chat_ids.append(int(chat_id))
        except (TypeError, ValueError):
            logging.warning("Holiday digest: invalid chat id in settings: %r", chat_id)
    return enabled_chat_ids


def _json_list_from_value(value) -> list[dict] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("holidays", "items", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return None


def _extract_json_list(text: str) -> list[dict]:
    """Extract a JSON list even when the model wraps it in prose or an object."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model response")

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(raw)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        extracted = _json_list_from_value(parsed)
        if extracted is not None:
            return extracted

        starts = sorted(
            position
            for position in (candidate.find("["), candidate.find("{"))
            if position >= 0
        )
        for start in starts:
            try:
                parsed, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            extracted = _json_list_from_value(parsed)
            if extracted is not None:
                return extracted

    raise ValueError("model response does not contain a JSON holiday list")


def _build_digest_prompt(holidays: list[Holiday]) -> str:
    source = "\n\n".join(
        f"{index}. {holiday.title}\n"
        f"Категория: {holiday.category or 'не указана'}\n"
        f"Описание с calend.ru: {holiday.description}"
        for index, holiday in enumerate(holidays, 1)
    )
    return (
        "Сделай ежедневную рассылку праздников для Telegram по данным ниже. "
        "Для каждого праздника верни короткое описание на русском: 1-3 предложения, без вводной болтовни. "
        "Не добавляй праздники от себя и сохрани порядок исходного списка. "
        "Стиль descriptions должен соответствовать текущему промпту чата. "
        "Ответ верни строго валидным JSON-массивом без markdown. "
        "Для связи с исходным праздником обязательно копируй его числовой id; название повторять не нужно. "
        'Формат: [{"id": 1, "description": "краткое описание"}].\n\n'
        f"Данные:\n{source}"
    )


def _resolve_generated_holiday_title(
    item: dict,
    index: int,
    holidays: list[Holiday],
    title_lookup: dict[str, str],
    *,
    allow_position_fallback: bool,
) -> tuple[str | None, str]:
    raw_id = item.get("id")
    try:
        holiday_id = int(raw_id)
    except (TypeError, ValueError):
        holiday_id = 0
    if 1 <= holiday_id <= len(holidays):
        return holidays[holiday_id - 1].title, "id"

    generated_title = _normalize_text(str(item.get("title", "")))
    if generated_title:
        source_title = title_lookup.get(_normalize_title_key(generated_title))
        if source_title:
            return source_title, "normalized title"

    if allow_position_fallback and index < len(holidays):
        return holidays[index].title, "position"

    return None, "unmatched"


def _map_generated_descriptions(
    generated: list[dict],
    holidays: list[Holiday],
    chat_id: int,
) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    title_lookup = {
        _normalize_title_key(holiday.title): holiday.title
        for holiday in holidays
        if _normalize_title_key(holiday.title)
    }
    allow_position_fallback = len(generated) == len(holidays)

    for index, item in enumerate(generated):
        if not isinstance(item, dict):
            continue
        description = _normalize_text(str(item.get("description", "")))
        if not description:
            continue

        source_title, match_method = _resolve_generated_holiday_title(
            item,
            index,
            holidays,
            title_lookup,
            allow_position_fallback=allow_position_fallback,
        )
        if not source_title or source_title in descriptions:
            continue
        descriptions[source_title] = description
        if match_method == "position":
            logging.info(
                "Holiday digest: matched generated item %s to %r by position for chat %s",
                index + 1,
                source_title,
                chat_id,
            )

    return descriptions


@ai_feature("праздники")
async def generate_holiday_descriptions(holidays: list[Holiday], chat_id: int) -> dict[str, str]:
    if not holidays:
        return {}

    descriptions: dict[str, str] = {}
    pending = list(holidays)

    for attempt in range(1, HOLIDAY_AI_ATTEMPTS + 1):
        if not pending:
            break

        task_prompt = _build_digest_prompt(pending)
        if attempt > 1:
            task_prompt += (
                "\n\nЭто повторная попытка: предыдущий ответ не удалось полностью разобрать. "
                "КРИТИЧЕСКИ ВАЖНО вернуть только валидный JSON-массив указанного формата, "
                "без пояснений, markdown и текста вне JSON. Стиль описаний всё равно должен "
                "соответствовать текущему промпту чата."
            )

        prompt = build_prompt_with_current_chat_prompt(
            str(chat_id),
            task_prompt,
            task_name="ежедневную рассылку праздников",
        )

        response_text = ""
        try:
            response_text = await generate_simple_response(prompt, str(chat_id))
            generated = _extract_json_list(response_text)
        except Exception as e:
            logging.warning(
                "Holiday digest: AI attempt %s/%s failed for chat %s. "
                "Reason: %s. Response preview: %r",
                attempt,
                HOLIDAY_AI_ATTEMPTS,
                chat_id,
                e,
                response_text[:300],
                exc_info=True,
            )
            continue

        attempt_descriptions = _map_generated_descriptions(generated, pending, chat_id)
        descriptions.update(attempt_descriptions)
        pending = [holiday for holiday in holidays if holiday.title not in descriptions]

        if pending:
            logging.warning(
                "Holiday digest: AI attempt %s/%s incomplete for chat %s (%s/%s total generated); "
                "retrying missing holidays: %s",
                attempt,
                HOLIDAY_AI_ATTEMPTS,
                chat_id,
                len(descriptions),
                len(holidays),
                "; ".join(holiday.title for holiday in pending),
            )

    if pending:
        logging.warning(
            "Holiday digest: AI descriptions incomplete for chat %s after %s attempts (%s/%s generated); "
            "using calend.ru descriptions for: %s",
            chat_id,
            HOLIDAY_AI_ATTEMPTS,
            len(descriptions),
            len(holidays),
            "; ".join(holiday.title for holiday in pending),
        )

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


async def send_daily_holidays(bot: Bot, chat_id: int, holidays: list[Holiday] | None = None) -> None:
    holidays = holidays if holidays is not None else await fetch_today_holidays()
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


async def schedule_daily_holidays(bot: Bot) -> None:
    while True:
        moscow_tz = pytz.timezone("Europe/Moscow")
        now = datetime.now().astimezone(moscow_tz)

        target_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            holidays = await fetch_today_holidays()
            if not holidays:
                logging.warning("Holiday digest: calend.ru returned no holidays")
                continue

            chat_ids = get_holiday_broadcast_chat_ids()
            if not chat_ids:
                logging.info("Holiday digest: no chats have holiday broadcasts enabled")
                continue

            for chat_id in chat_ids:
                try:
                    await send_daily_holidays(bot, chat_id, holidays)
                except Exception as e:
                    logging.error(
                        f"Holiday digest scheduler failed for chat {chat_id}: {e}",
                        exc_info=True,
                    )
        except Exception as e:
            logging.error(f"Holiday digest scheduler failed: {e}", exc_info=True)