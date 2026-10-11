"""Delivery of dialogue replies to Telegram.

In participant-imitation mode the model writes a «серия» (several messages in a
row) as one text with one message per line. Real people send such a series as
separate Telegram messages with typing pauses, so this module splits the reply
and sends it the same way, at the imitated participant's own measured pace.
Every other prompt keeps the single reply.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable


SERIES_MAX_PARTS = 6
# Measured in production chats: the median pause between messages of a human
# series is ~12 s, i.e. ~1.5–2.2 chars/s including thinking time. Used when the
# participant has too little series history of their own.
DEFAULT_SERIES_RATES = (0.8, 1.1, 1.4, 1.6, 1.9, 2.2, 2.7, 3.4, 4.2)
DEFAULT_SERIES_MIN_GAP_SECONDS = 3.0
SERIES_MAX_DELAY_SECONDS = 60.0
SERIES_TOTAL_DELAY_SECONDS = 150.0
# Telegram shows "typing…" for ~5 s per chat action; refresh before it fades.
TYPING_REFRESH_SECONDS = 4.5


def split_participant_series(text: str, *, max_parts: int = SERIES_MAX_PARTS) -> list[str]:
    """Split a participant reply into series messages (one per non-empty line).

    Code blocks are never split. When the model wrote more lines than a real
    series would have, the tail is merged into the last message instead of
    flooding the chat.
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    if "```" in stripped:
        return [stripped]

    parts = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(parts) <= 1:
        return [stripped]
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + ["\n".join(parts[max_parts - 1 :])]
    return parts


def series_delay(part: str, timing: dict | None = None, rng: random.Random | None = None) -> float:
    """Seconds a person would take to think and type ``part`` before sending it.

    The pace is sampled from the participant's real series (chars per second
    measured between their consecutive messages), so both fast and slow typers
    keep their own rhythm and pauses vary naturally from message to message.
    """
    rng = rng or random
    timing = timing or {}
    rates = [float(rate) for rate in timing.get("rates") or () if float(rate) > 0] or list(DEFAULT_SERIES_RATES)
    min_gap = float(timing.get("min_gap") or DEFAULT_SERIES_MIN_GAP_SECONDS)
    rate = rng.choice(rates)
    delay = len(part) / rate * rng.uniform(0.85, 1.15)
    return max(min_gap, min(SERIES_MAX_DELAY_SECONDS, delay))


async def _show_typing_for(bot, chat_id, seconds: float, sleep) -> None:
    remaining = seconds
    while remaining > 0:
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        step = min(TYPING_REFRESH_SECONDS, remaining)
        await sleep(step)
        remaining -= step


async def send_dialog_response(
    message,
    response: str,
    *,
    settings: dict | None,
    bot,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> None:
    """Reply to ``message``; in participant mode send a multi-line reply as a series."""
    settings = settings or {}
    if settings.get("prompt_type") != "user_style":
        await message.reply(response)
        return

    parts = split_participant_series(response)
    if len(parts) <= 1:
        await message.reply(parts[0] if parts else response)
        return

    chat_id = message.chat.id
    timing = settings.get("style_series_timing")
    await message.reply(parts[0])
    delay_budget = SERIES_TOTAL_DELAY_SECONDS
    for part in parts[1:]:
        try:
            delay = series_delay(part, timing, rng)
            floor = float((timing or {}).get("min_gap") or DEFAULT_SERIES_MIN_GAP_SECONDS)
            delay = max(min(delay, delay_budget), floor)
            delay_budget -= delay
            await _show_typing_for(bot, chat_id, delay, sleep)
            await bot.send_message(chat_id, part)
        except Exception as exc:  # one failed part must not crash the handler
            logging.warning("Participant series delivery failed chat=%s: %s", chat_id, exc)
            return
