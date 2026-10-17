"""Shared Telegram helpers for every World of Upupa router."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from core.loader import bot
from features.world.permissions import is_chat_admin, is_strict_chat_admin
from features.world.presentation import split_text
from features.world.service import get_world_service


_busy: set[str] = set()


def title_of(message: types.Message) -> str:
    return message.chat.title or "Безымянное государство"


def is_group(message: types.Message) -> bool:
    return getattr(message.chat, "type", None) in {"group", "supergroup"}


def user_label(user) -> str:
    if user is None:
        return "неизвестный гражданин"
    return user.full_name or (f"@{user.username}" if getattr(user, "username", None) else str(user.id))


def replied_bot_text(message: types.Message) -> str:
    """Text of the bot message this message replies to, or '' for anything else."""
    replied = getattr(message, "reply_to_message", None)
    if replied is None or getattr(message, "from_user", None) is None:
        return ""
    author = getattr(replied, "from_user", None)
    if author is None or not getattr(author, "is_bot", False):
        return ""
    return (getattr(replied, "text", None) or getattr(replied, "caption", None) or "").strip()


async def require_world(message: types.Message, *, refresh_title: bool = False):
    """Return ``(service, state)`` for a participating group, explaining otherwise."""
    if not is_group(message):
        await message.reply("🌍 Мир Упупы работает только в групповых чатах.")
        return None
    service = get_world_service()
    state = await service.get_state(message.chat.id, title_of(message) if refresh_title else None)
    if state is None or not state.enabled:
        await message.reply(
            "🌍 Этот чат пока не участвует в Мире Упупы. "
            "Админ может включить его через «упупа настройки»."
        )
        return None
    return service, state


async def callback_state(query: types.CallbackQuery):
    """``(service, state)`` for the chat a button lives in, or an alert."""
    if query.message is None or query.from_user is None:
        return None
    service = get_world_service()
    state = await service.get_state(query.message.chat.id)
    if state is None or not state.enabled:
        await query.answer("Этот чат сейчас вне Мира Упупы.", show_alert=True)
        return None
    return service, state


async def is_diplomat(chat_id: int, user_id: int) -> bool:
    """Admins, the creator, the bot owner and the appointed ambassador."""
    return await is_chat_admin(bot, chat_id, user_id)


async def is_admin(chat_id: int, user_id: int) -> bool:
    """Only real Telegram admins/creator and the bot owner (never the ambassador)."""
    return await is_strict_chat_admin(bot, chat_id, user_id)


async def require_diplomat(query: types.CallbackQuery) -> bool:
    if await is_diplomat(query.message.chat.id, query.from_user.id):
        return True
    await query.answer(
        "Это дипломатическое решение доступно администраторам и назначенному послу.",
        show_alert=True,
    )
    return False


async def safe_edit(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = None,
) -> None:
    """Edit a menu in place; never crash on «not modified», photos or long text."""
    chunks = split_text(text)
    head, tail = chunks[0], chunks[1:]
    can_edit = getattr(message, "text", None) is not None
    try:
        if can_edit:
            await message.edit_text(
                head,
                reply_markup=None if tail else reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.answer(head, reply_markup=None if tail else reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            logging.warning("World menu edit failed, sending a new message: %s", error)
            await message.answer(head, reply_markup=None if tail else reply_markup, parse_mode=parse_mode)
    for index, chunk in enumerate(tail):
        last = index == len(tail) - 1
        await message.answer(chunk, reply_markup=reply_markup if last else None, parse_mode=parse_mode)


async def reply_long(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = None,
) -> None:
    chunks = split_text(text)
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        if index == 0:
            await message.reply(chunk, reply_markup=reply_markup if last else None, parse_mode=parse_mode)
        else:
            await message.answer(chunk, reply_markup=reply_markup if last else None, parse_mode=parse_mode)


@asynccontextmanager
async def single_flight(key: str):
    """Yield False when the same expensive action is already running (double taps)."""
    if key in _busy:
        yield False
        return
    _busy.add(key)
    try:
        yield True
    finally:
        _busy.discard(key)


async def answer_quietly(query: types.CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except TelegramBadRequest:
        # «query is too old» after a slow model call is harmless.
        pass
