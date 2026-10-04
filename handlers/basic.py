"""Хэндлеры: Базовые команды: start, справка, настройки, управление чатами.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import logging
from time import monotonic

from aiogram import F, types
from aiogram.filters import CommandStart
from core.loader import bot
from core.settings import ADMIN_ID, BLOCKED_USERS
from core.state import chat_settings, conversation_history
from core.upupa_utils import normalize_upupa_command
from features.common_settings import process_leave_chat, process_leave_empty_chats
from features.chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, remove_chat
)
from features.group_bans import is_group_banned, unban_group
from features.interactive_settings import send_settings_menu, handle_settings_callback, send_help_menu, handle_help_callback
from features.world.service import get_world_service
from services.holidays import process_holidays_command as process_holidays_service

router = Router(name="basic")

_LEAVE_NOTIFICATION_DEDUPE_SECONDS = 10.0
_recent_leave_notifications = {}


def _is_present_member(chat_member) -> bool:
    status = getattr(chat_member, "status", None)
    if status in {"member", "administrator", "creator"}:
        return True
    if status == "restricted":
        return bool(getattr(chat_member, "is_member", False))
    return False


def _is_member_leave_update(update: types.ChatMemberUpdated) -> bool:
    return (
        _is_present_member(update.old_chat_member)
        and getattr(update.new_chat_member, "status", None) == "left"
    )


def _member_display_name(member) -> str:
    full_name = getattr(member, "full_name", None)
    display_name = str(full_name).strip() if full_name else ""
    if display_name:
        return display_name

    username = getattr(member, "username", None)
    return f"@{username}" if username else str(member.id)


def _claim_leave_notification(chat_id: int, user_id: int) -> bool:
    now = monotonic()
    cutoff = now - _LEAVE_NOTIFICATION_DEDUPE_SECONDS
    stale = [key for key, seen_at in _recent_leave_notifications.items() if seen_at <= cutoff]
    for key in stale:
        _recent_leave_notifications.pop(key, None)

    key = (int(chat_id), int(user_id))
    seen_at = _recent_leave_notifications.get(key)
    if seen_at is not None and now - seen_at < _LEAVE_NOTIFICATION_DEDUPE_SECONDS:
        return False

    _recent_leave_notifications[key] = now
    return True


async def _notify_member_left(chat_id: int, member, send_message) -> bool:
    if member is None or member.is_bot:
        return False

    chat_id_str = str(chat_id)
    if not chat_settings.get(chat_id_str, {}).get("leave_notifications_enabled", False):
        return False

    if not _claim_leave_notification(chat_id, member.id):
        return False

    await send_message(f"этот пидорас {_member_display_name(member)} только что убежал")
    return True


# ================== БЛОК 5.1: БАЗОВЫЕ КОМАНДЫ ==================

@router.message(CommandStart())
async def process_start_command(message: types.Message):
    await message.reply("Я пидорас")

@router.message(lambda message: message.text is not None and message.text.lower() == "очистка" and message.from_user.id not in BLOCKED_USERS)
async def process_clear_command(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in conversation_history:
        conversation_history[chat_id] = []
        await message.reply("Смыто всё говно")
    else:
        await message.reply("История и так пустая, долбоёб")

@router.message(lambda message: message.text is not None and message.text.strip().lower() == "праздники" and message.from_user.id not in BLOCKED_USERS)
async def holidays_command_handler(message: types.Message):
    await process_holidays_service(message)

# ================== БЛОК 5.2: СПРАВКА И НАСТРОЙКИ ==================

@router.message(lambda message: message.text and message.text.lower() in ["чоумееш", "справка", "help", "помощь"] and message.from_user.id not in BLOCKED_USERS)
async def handle_chooumeesh(message: types.Message):
    await send_help_menu(message)
    
@router.callback_query(F.data.startswith("help:"))
async def help_callback_handler(query: types.CallbackQuery):
    await handle_help_callback(query)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа настройки")
async def settings_command_handler(message: types.Message):
    await send_settings_menu(message)

@router.callback_query(F.data.startswith("settings:"))
async def settings_callback_handler(query: types.CallbackQuery):
    await handle_settings_callback(query)

# ================== БЛОК 5.3: УПРАВЛЕНИЕ ЧАТАМИ ==================

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа выйди из чатов хуесосов")
async def leave_empty_chats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    await process_leave_empty_chats(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа выйди из "))
async def leave_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    # Извлекаем идентификатор чата из нормализованного текста
    normalized = normalize_upupa_command(message.text)
    chat_identifier = normalized[len("упупа выйди из "):].strip()
    await process_leave_chat(message, chat_identifier)
    
@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа разбань "))
async def unban_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    normalized = normalize_upupa_command(message.text)
    identifier = normalized[len("упупа разбань "):].strip()
    chat = unban_group(identifier)
    if chat is None:
        await message.reply("Такой группы в бане нет.")
        return
    label = chat.get("title") or (f"@{chat['username']}" if chat.get("username") else str(chat["id"]))
    await message.reply(f"Разбанил {label}")

@router.message(lambda message: message.text and message.text.lower() == "обновить чаты")
async def update_all_chats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Иди нахуй, у тебя нет прав на это.")
        return
    await process_update_all_chats(message, bot)

@router.message(lambda message: message.text and message.text.lower() == "где сидишь")
async def handle_where_sits(message: types.Message):
    response = get_chats_list(message.chat.id, message.chat.title, message.chat.username)
    await message.reply(response)


@router.message(F.left_chat_member)
async def handle_left_chat_member(message: types.Message):
    """Fallback для сервисного сообщения об уходе в небольших чатах."""
    await _notify_member_left(
        message.chat.id,
        message.left_chat_member,
        message.answer,
    )


@router.chat_member()
async def handle_chat_member_update(update: types.ChatMemberUpdated):
    """Отследить реальный выход участника независимо от сервисного сообщения."""
    if not _is_member_leave_update(update):
        return

    member = update.new_chat_member.user
    await _notify_member_left(
        update.chat.id,
        member,
        lambda text: bot.send_message(update.chat.id, text),
    )


@router.my_chat_member()
async def handle_my_chat_member_update(update: types.ChatMemberUpdated):
    chat = update.chat
    new_status = update.new_chat_member.status

    if new_status in ["left", "kicked"]:
        removed = remove_chat(chat.id)
        try:
            await get_world_service().disable_state(chat.id)
        except RuntimeError:
            # Startup tests/imports can observe the handler before composition root wiring.
            pass
        if removed:
            logging.info(f"Bot removed from chat {chat.title or chat.id} ({chat.id}); chat was pruned from lists.")
    elif new_status in ["member", "administrator", "creator"]:
        if is_group_banned(chat.id):
            logging.info("Banned group tried to add bot again: %s (%s)", chat.title or chat.id, chat.id)
            try:
                await bot.leave_chat(chat.id)
            except Exception as exc:
                logging.error("Failed to leave banned group %s: %s", chat.id, exc)
            remove_chat(chat.id)
            return
        add_chat(chat.id, chat.title, chat.username)

# ================== БЛОК 5.4: СМС И ММС ==================
