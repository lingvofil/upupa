"""Delivery of dialogue replies to Telegram.

In participant-imitation mode the model writes a «серия» (several messages in a
row) as one text with one message per line. Real people send such a series as
separate Telegram messages with short typing pauses, so this module splits the
reply and sends it the same way. Every other prompt keeps the single reply.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


SERIES_MAX_PARTS = 6
SERIES_MIN_DELAY_SECONDS = 0.8
SERIES_MAX_DELAY_SECONDS = 4.0
SERIES_TOTAL_DELAY_SECONDS = 12.0
# Rough human typing speed in a messenger, characters per second.
SERIES_TYPING_CHARS_PER_SECOND = 25.0


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


def typing_delay(part: str) -> float:
    seconds = len(part) / SERIES_TYPING_CHARS_PER_SECOND
    return max(SERIES_MIN_DELAY_SECONDS, min(SERIES_MAX_DELAY_SECONDS, seconds))


async def send_dialog_response(
    message,
    response: str,
    *,
    settings: dict | None,
    bot,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Reply to ``message``; in participant mode send a multi-line reply as a series."""
    if (settings or {}).get("prompt_type") != "user_style":
        await message.reply(response)
        return

    parts = split_participant_series(response)
    if len(parts) <= 1:
        await message.reply(parts[0] if parts else response)
        return

    chat_id = message.chat.id
    await message.reply(parts[0])
    delay_budget = SERIES_TOTAL_DELAY_SECONDS
    for part in parts[1:]:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            delay = min(typing_delay(part), max(delay_budget, 0.0))
            if delay > 0:
                await sleep(delay)
                delay_budget -= delay
            await bot.send_message(chat_id, part)
        except Exception as exc:  # one failed part must not crash the handler
            logging.warning("Participant series delivery failed chat=%s: %s", chat_id, exc)
            return
