"""Media showcase replies during a World of Upupa state екскурсия."""

from __future__ import annotations

import logging
import re

from aiogram import Router, types

from core.loader import bot
from features.world.interactions import (
    finish_visit,
    get_open_visit,
    notify_visit_finished,
    record_interaction_event,
    visit_is_active,
)
from features.world.showcase import extract_showcase_content
from handlers.world import _require_world, _title


router = Router(name="world_visit_media")
VISIT_PROMPT_PREFIX = "🛬 Государственный визит"
_VISIT_TARGET_RE = re.compile(r"Гости: государство №(\d+)")


def _is_visit_media_showcase_reply(message: types.Message) -> bool:
    replied = message.reply_to_message
    if replied is None or message.from_user is None:
        return False
    replied_text = (replied.text or replied.caption or "").strip()
    showcase = extract_showcase_content(message)
    return bool(
        showcase is not None
        and showcase.is_media
        and replied_text.startswith(VISIT_PROMPT_PREFIX)
    )


async def _active_visit_or_close(service, host_world_id: int, guest_world_id: int):
    visit = await get_open_visit(service, host_world_id, guest_world_id)
    if visit is None or visit_is_active(visit):
        return visit
    closed = await finish_visit(
        service,
        host_world_id,
        guest_world_id,
        reason="timeout",
    )
    if closed is not None:
        await notify_visit_finished(bot, service, closed, reason="timeout")
    return None


@router.message(_is_visit_media_showcase_reply)
async def visit_media_showcase(message: types.Message):
    """Copy Telegram media to the visiting state and journal a text representation."""
    service = await _require_world(message)
    if service is None or message.from_user is None or message.reply_to_message is None:
        return

    replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    match = _VISIT_TARGET_RE.search(replied_text)
    if match is None:
        return

    guest_world_id = int(match.group(1))
    host = await service.get_state(message.chat.id, _title(message))
    guest = await service.get_state_by_world_id(guest_world_id)
    if host is None or guest is None or not guest.enabled:
        await message.reply("🎒 Делегация уже куда-то уехала. Показывать некому.")
        return

    visit = await _active_visit_or_close(service, host.world_id, guest.world_id)
    if visit is None:
        await message.reply("🎒 Екскурсия уже закончилась. Делегация уехала, поздно показывать достопримечательности.")
        return

    showcase = extract_showcase_content(message)
    if showcase is None or not showcase.is_media:
        return

    name = message.from_user.full_name or (
        f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    )
    await record_interaction_event(
        service,
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={
            "user_id": message.from_user.id,
            "user_name": name,
            "text": showcase.text,
            "media_type": showcase.media_type or "",
            "telegram_message_id": message.message_id,
        },
    )

    try:
        await bot.send_message(
            guest.chat_id,
            f"🎒 Екскурсия по государству №{host.world_id} — {host.title}.\n\n"
            f"{name} показал вам: {showcase.text}",
        )
    except Exception:
        logging.exception("World visit media caption delivery failed guest=%s", guest.world_id)

    media_delivered = True
    try:
        await bot.copy_message(
            chat_id=guest.chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        media_delivered = False
        logging.exception(
            "World visit media copy failed host=%s guest=%s message=%s",
            host.world_id,
            guest.world_id,
            message.message_id,
        )

    if media_delivered:
        await message.reply("🎒 Показ засчитан и отправлен делегации.")
    else:
        await message.reply("🎒 Показ записал, но само медиа до делегации не долетело.")
