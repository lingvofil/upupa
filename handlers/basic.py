"""Хэндлеры: Базовые команды: start, справка, настройки, управление чатами.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Filter
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from core.upupa_utils import normalize_upupa_command
from features.common_settings import process_leave_chat, process_leave_empty_chats
from features.chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, save_chat_settings, remove_chat
)
from features.interactive_settings import send_settings_menu, handle_settings_callback, send_help_menu, handle_help_callback

router = Router(name="basic")


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

@router.my_chat_member()
async def handle_my_chat_member_update(update: types.ChatMemberUpdated):
    chat = update.chat
    new_status = update.new_chat_member.status

    if new_status in ["left", "kicked"]:
        removed = remove_chat(chat.id)
        if removed:
            logging.info(f"Bot removed from chat {chat.title or chat.id} ({chat.id}); chat was pruned from ???????.")
    elif new_status in ["member", "administrator", "creator"]:
        add_chat(chat.id, chat.title, chat.username)

# ================== БЛОК 5.4: СМС И ММС ==================
