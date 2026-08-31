"""Telegram permission checks for World of Upupa administration actions."""

from __future__ import annotations

import logging

from aiogram import Bot

from core.settings import ADMIN_ID


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True for the bot owner or Telegram admins/creators of the chat."""
    if user_id == ADMIN_ID:
        return True

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


async def is_world_diplomat(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Admins/owner and the appointed ambassador may make diplomatic decisions."""
    if await is_chat_admin(bot, chat_id, user_id):
        return True
    try:
        from features.world.service import get_world_service

        return await get_world_service().is_ambassador(chat_id, user_id)
    except Exception as exc:
        logging.warning(
            "World ambassador permission check failed chat_id=%s user_id=%s: %s",
            chat_id,
            user_id,
            exc,
        )
        return False
