"""Telegram permission checks for World of Upupa administration actions."""

from __future__ import annotations

import logging

from aiogram import Bot

from core.settings import ADMIN_ID


async def _is_appointed_ambassador(chat_id: int, user_id: int) -> bool:
    try:
        from features.world.service import get_world_service

        return await get_world_service().is_ambassador(chat_id, user_id)
    except Exception:
        return False


async def is_strict_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True only for the bot owner or Telegram admins/creators."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception as exc:
        logging.warning(
            "World strict admin check failed chat_id=%s user_id=%s: %s",
            chat_id,
            user_id,
            exc,
        )
        return False
    return member.status in {"administrator", "creator"}


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """World diplomacy permission: owner/admins plus the appointed ambassador.

    Kept under the historical name because existing diplomacy handlers call this
    helper. Settings have their own strict permission guard; ambassador status
    does not unlock the settings menu.
    """
    if await is_strict_chat_admin(bot, chat_id, user_id):
        return True
    return await _is_appointed_ambassador(chat_id, user_id)


async def is_world_diplomat(bot: Bot, chat_id: int, user_id: int) -> bool:
    return await is_chat_admin(bot, chat_id, user_id)
