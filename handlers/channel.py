"""Админская команда принудительной публикации в канале Упупы."""

import logging

from aiogram import Router, types

from core.settings import ADMIN_ID
from features.channel.cringedep_service import CHANNEL_TARGET, publish_channel_post

router = Router(name="channel")


@router.message(lambda message: message.text and message.text.strip().lower() == "канал пост")
async def force_channel_post(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    status = await message.reply("щас")
    try:
        sent, _text = await publish_channel_post(message.bot, source="manual")
        message_id = getattr(sent, "message_id", None)
        if message_id and CHANNEL_TARGET.startswith("@"):
            username = CHANNEL_TARGET[1:]
            await status.edit_text(f"запостил\nhttps://t.me/{username}/{message_id}")
        else:
            await status.edit_text("запостил")
    except Exception as exc:
        logging.error("[channel] manual post failed: %s", exc, exc_info=True)
        await status.edit_text(f"не запостилось: {exc}")
