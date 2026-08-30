"""Autonomous polls for Upupa's public channel and delayed reactions to results."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.settings import SPECIAL_CHAT_ID
from features.channel.mood import mood_prompt
from features.channel.storage import append_post
from prompts.channel import CHANNEL_PERSONA

POLL_STATE_FILE = Path("channel_polls.json")
POLL_PROBABILITY = 0.06
POLL_COOLDOWN_MIN_POSTS = 15
POLL_COOLDOWN_MAX_POSTS = 25
POLL_DURATION_HOURS = 12
REFLECTION_DELAY_MINUTES = 30
REFLECTION_DELAY_MAX_HOURS = 6
MAX_GENERATION_ATTEMPTS = 3
MAX_QUESTION_LENGTH = 180
MAX_OPTION_LENGTH = 70
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_REFLECTION_LENGTH = 240
MAX_STORED_POLLS = 50

_state_lock = asyncio.Lock()

POLL_PROMPT = """
Ты иногда вместо обычного поста устраиваешь в своём Telegram-канале анонимный опрос.
Придумай один короткий опрос в характере Упупы: живой, хулиганский, абсурдный, бытовой или слегка
ехидный. Это не социологическое исследование и не просьба аудитории выбрать тему для полезного контента.
Можно спрашивать про нелепые решения, предметы, состояния, привычки или то, кем тебе сегодня быть.
Не проси персональные данные, не упоминай реальные имена, usernames и конкретные чаты.

Нужно от 2 до 4 коротких вариантов. Они должны реально различаться и быть понятными без пояснений.
Опрос будет анонимным. Не добавляй вступление, вывод, хэштеги или нумерацию.

Ответь СТРОГО в формате:
ВОПРОС: <вопрос>
ВАРИАНТ: <вариант 1>
ВАРИАНТ: <вариант 2>
[ВАРИАНТ: <вариант 3>]
[ВАРИАНТ: <вариант 4>]
""".strip()

REFLECTION_PROMPT = """
Ты — Упупа. Некоторое время назад ты устроил в своём Telegram-канале опрос, теперь голосование закрыто.
Напиши один короткий самостоятельный пост-реакцию на итог. Это не отчёт и не перечисление статистики:
заметь самый смешной, неожиданный или показательный результат и сделай свой вывод. Можно спорить с
подписчиками, обижаться, торжествовать, менять мнение или объявить результат законом природы. Разрешён мат.
Если голосов не было, тоже отреагируй на это. Не выдумывай цифры и не называй людей.
Жёсткий максимум — 240 символов. Не добавляй заголовок и не объясняй контекст служебным языком.
""".strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_state() -> dict:
    try:
        with POLL_STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"polls": [], "next_eligible_post_count": 0}
    if not isinstance(data, dict):
        return {"polls": [], "next_eligible_post_count": 0}
    polls = data.get("polls")
    if not isinstance(polls, list):
        polls = []
    return {
        "polls": [item for item in polls if isinstance(item, dict)][-MAX_STORED_POLLS:],
        "next_eligible_post_count": int(data.get("next_eligible_post_count") or 0),
    }


def _write_state(state: dict) -> None:
    POLL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = POLL_STATE_FILE.with_name(POLL_STATE_FILE.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, POLL_STATE_FILE)


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_active_poll(state: dict) -> bool:
    return any(item.get("status") == "active" for item in state.get("polls", []))


def _should_try_poll(published_posts: list[dict], state: dict, *, rng=random) -> bool:
    if _has_active_poll(state):
        return False
    if len(published_posts) < int(state.get("next_eligible_post_count") or 0):
        return False
    return rng.random() < POLL_PROBABILITY


def _parse_poll_plan(raw: str) -> tuple[str, list[str]] | None:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return None
    if not lines[0].casefold().startswith("вопрос:"):
        return None
    question = lines[0].split(":", 1)[1].strip().strip('"«»')
    options: list[str] = []
    for line in lines[1:]:
        if not line.casefold().startswith("вариант:"):
            return None
        option = line.split(":", 1)[1].strip().strip('"«»')
        if option:
            options.append(option)
    if not question or not options:
        return None
    return question, options


def _validate_poll_plan(question: str, options: list[str]) -> str | None:
    question = question.strip()
    clean_options = [option.strip() for option in options]
    if not question:
        return "пустой вопрос"
    if len(question) > MAX_QUESTION_LENGTH:
        return f"вопрос длиннее {MAX_QUESTION_LENGTH} символов"
    if not MIN_OPTIONS <= len(clean_options) <= MAX_OPTIONS:
        return f"нужно {MIN_OPTIONS}–{MAX_OPTIONS} вариантов"
    if any(not option for option in clean_options):
        return "есть пустой вариант"
    if any(len(option) > MAX_OPTION_LENGTH for option in clean_options):
        return f"вариант длиннее {MAX_OPTION_LENGTH} символов"
    if len({option.casefold() for option in clean_options}) != len(clean_options):
        return "варианты повторяются"
    if any("http://" in option.casefold() or "https://" in option.casefold() or "@" in option for option in [question, *clean_options]):
        return "в опросе не должно быть ссылок или usernames"
    return None


def _poll_prompt(mood: dict, retry_note: str = "") -> str:
    retry = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Сделай другой опрос."
        if retry_note
        else ""
    )
    return (
        f"{CHANNEL_PERSONA}\n\n"
        "ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:\n"
        f"{mood_prompt(mood)}\n"
        "Не называй это состояние аудитории, только дай ему повлиять на вопрос и варианты.\n\n"
        f"{POLL_PROMPT}{retry}"
    )


async def prepare_poll(published_posts: list[dict], mood: dict, *, rng=random) -> dict | None:
    """Return a validated poll plan when this publication slot should become a poll."""
    async with _state_lock:
        state = await asyncio.to_thread(_read_state)
        if not _should_try_poll(published_posts, state, rng=rng):
            return None

    from AI.summarize import _generate_with_active_model

    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(_poll_prompt(mood, retry_note), str(SPECIAL_CHAT_ID))
        parsed = _parse_poll_plan(raw or "")
        if parsed is None:
            reason = "нужны строки ВОПРОС и 2–4 строки ВАРИАНТ"
        else:
            question, options = parsed
            reason = _validate_poll_plan(question, options)
        if not reason:
            return {"question": question, "options": options}
        logging.warning("[channel] poll plan attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    logging.warning("[channel] poll generation exhausted, falling back to regular post")
    return None


async def register_published_poll(
    sent,
    *,
    plan: dict,
    source: str,
    published_count_before: int,
    rng=random,
) -> None:
    """Persist enough Telegram identifiers to finish the poll after a restart."""
    poll = getattr(sent, "poll", None)
    poll_id = getattr(poll, "id", None)
    message_id = getattr(sent, "message_id", None)
    if not poll_id or message_id is None:
        raise ValueError("Telegram did not return poll id/message id")

    now = _utcnow()
    record = {
        "status": "active",
        "poll_id": str(poll_id),
        "message_id": int(message_id),
        "source": source,
        "question": str(plan["question"]),
        "options": [str(option) for option in plan["options"]],
        "created_at": now.isoformat(),
        "closes_at": (now + timedelta(hours=POLL_DURATION_HOURS)).isoformat(),
    }

    async with _state_lock:
        state = await asyncio.to_thread(_read_state)
        state.setdefault("polls", []).append(record)
        state["polls"] = state["polls"][-MAX_STORED_POLLS:]
        cooldown = rng.randint(POLL_COOLDOWN_MIN_POSTS, POLL_COOLDOWN_MAX_POSTS)
        state["next_eligible_post_count"] = published_count_before + 1 + cooldown
        await asyncio.to_thread(_write_state, state)


def _extract_results(poll) -> tuple[list[dict], int]:
    results: list[dict] = []
    total = int(getattr(poll, "total_voter_count", 0) or 0)
    for option in getattr(poll, "options", []) or []:
        results.append({
            "text": str(getattr(option, "text", "") or ""),
            "voter_count": int(getattr(option, "voter_count", 0) or 0),
        })
    return results, total


def _results_for_prompt(record: dict) -> str:
    total = int(record.get("total_voter_count") or 0)
    lines = [f"ВОПРОС: {record.get('question', '')}", f"ВСЕГО ГОЛОСОВ: {total}"]
    for result in record.get("results", []):
        count = int(result.get("voter_count") or 0)
        percent = round((count / total) * 100) if total else 0
        lines.append(f"- {result.get('text', '')}: {count} ({percent}%)")
    return "\n".join(lines)


def _validate_reflection(text: str) -> str | None:
    clean = (text or "").strip()
    if not clean:
        return "пустая реакция"
    if len(clean) > MAX_REFLECTION_LENGTH:
        return f"реакция длиннее {MAX_REFLECTION_LENGTH} символов"
    lowered = clean.casefold()
    if "http://" in lowered or "https://" in lowered:
        return "реакция содержит ссылку"
    return None


async def _generate_reflection(record: dict) -> str | None:
    from AI.summarize import _generate_with_active_model

    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        retry = (
            f"\n\nПредыдущая попытка не прошла проверку: {retry_note}. Напиши другой вариант."
            if retry_note
            else ""
        )
        prompt = f"{CHANNEL_PERSONA}\n\n{REFLECTION_PROMPT}\n\nРЕЗУЛЬТАТЫ:\n{_results_for_prompt(record)}{retry}"
        raw = await _generate_with_active_model(prompt, str(SPECIAL_CHAT_ID))
        text = (raw or "").strip()
        reason = _validate_reflection(text)
        if not reason:
            return text
        logging.warning("[channel] poll reflection attempt %s rejected: %s", attempt, reason)
        retry_note = reason
    return None


def _reflection_delay(*, rng=random) -> timedelta:
    low = REFLECTION_DELAY_MINUTES * 60
    high = REFLECTION_DELAY_MAX_HOURS * 60 * 60
    return timedelta(seconds=rng.randint(low, high))


async def process_due_polls(bot, *, channel_target: str, rng=random, now: datetime | None = None) -> None:
    """Close due polls and later publish one AI reaction to their final results."""
    current = (now or _utcnow()).astimezone(timezone.utc)

    async with _state_lock:
        state = await asyncio.to_thread(_read_state)

    for record in list(state.get("polls", [])):
        if record.get("status") != "active":
            continue
        closes_at = _parse_dt(record.get("closes_at"))
        if closes_at is None or current < closes_at:
            continue
        try:
            final_poll = await bot.stop_poll(channel_target, int(record["message_id"]))
        except Exception as exc:
            logging.warning("[channel] failed to close poll message_id=%s: %s", record.get("message_id"), exc)
            continue

        results, total = _extract_results(final_poll)
        record["results"] = results
        record["total_voter_count"] = total
        record["closed_at"] = current.isoformat()
        record["reflection_due_at"] = (current + _reflection_delay(rng=rng)).isoformat()
        record["status"] = "awaiting_reflection"

        async with _state_lock:
            await asyncio.to_thread(_write_state, state)
        logging.info(
            "[channel] poll closed message_id=%s voters=%s reflection_due=%s",
            record.get("message_id"),
            total,
            record.get("reflection_due_at"),
        )

    for record in list(state.get("polls", [])):
        if record.get("status") != "awaiting_reflection":
            continue
        due_at = _parse_dt(record.get("reflection_due_at"))
        if due_at is None or current < due_at:
            continue

        try:
            text = await _generate_reflection(record)
        except Exception as exc:
            logging.warning("[channel] poll reflection generation failed: %s", exc, exc_info=True)
            text = None
        if not text:
            record["reflection_due_at"] = (current + timedelta(minutes=30)).isoformat()
            async with _state_lock:
                await asyncio.to_thread(_write_state, state)
            continue

        try:
            sent = await bot.send_message(channel_target, text)
        except Exception as exc:
            logging.warning("[channel] poll reflection send failed: %s", exc, exc_info=True)
            continue

        post_record = {
            "created_at": current.isoformat(),
            "source": "poll_reflection",
            "text": text,
            "message_id": getattr(sent, "message_id", None),
            "post_kind": "poll_reflection",
            "poll_message_id": record.get("message_id"),
            "poll_question": record.get("question"),
            "poll_results": record.get("results", []),
            "poll_total_voter_count": record.get("total_voter_count", 0),
        }
        await asyncio.to_thread(append_post, post_record)

        record["status"] = "reflected"
        record["reflection_message_id"] = getattr(sent, "message_id", None)
        record["reflected_at"] = current.isoformat()
        async with _state_lock:
            await asyncio.to_thread(_write_state, state)
        logging.info(
            "[channel] poll reflection published poll_message_id=%s reflection_message_id=%s",
            record.get("message_id"),
            record.get("reflection_message_id"),
        )
