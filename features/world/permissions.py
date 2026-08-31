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


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True for owner/admins and, for World diplomacy, the appointed ambassador.

    Interactive settings still have their own strict ``has_settings_permission`` guard,
    so ambassador status does not grant access to the bot settings menu.
    """
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
        return await _is_appointed_ambassador(chat_id, user_id)
    if member.status in {"administrator", "creator"}:
        return True
    return await _is_appointed_ambassador(chat_id, user_id)


async def is_world_diplomat(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Explicit alias for commands whose intent is diplomatic rather than settings access."""
    return await is_chat_admin(bot, chat_id, user_id)
