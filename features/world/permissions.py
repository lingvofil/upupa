"""Telegram permission checks for World of Upupa administration actions."""

from __future__ import annotations

import logging

from aiogram import Bot


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True only for Telegram administrators/creators of the current chat."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception as exc:
        logging.warning(
            "World permission check failed chat_id=%s user_id=%s: %s",
            chat_id,
            user_id,
            exc,
        )
        return False
    return member.status in {"administrator", "creator"}
