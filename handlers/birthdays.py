"""Хэндлеры: Дни рождения.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import Bot, Dispatcher, F, types
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from core.upupa_utils import normalize_upupa_command
from AI.birthday_calendar import (
    handle_birthday_command,
    handle_birthday_list_command,
    handle_test_greeting_command,
    handle_admin_birthday_list_command,
    birthday_scheduler
)

router = Router(name="birthdays")


# ================== БЛОК 6.7: ДНИ РОЖДЕНИЯ ==================
    
@router.message(lambda message: message.text and 
                (normalize_upupa_command(message.text).startswith("упупа запомни: мой др") or 
                 normalize_upupa_command(message.text).startswith("упупа запомни мой др")) and 
                 message.from_user.id not in BLOCKED_USERS)
async def handle_birthday_save_command(message: types.Message):
    await handle_birthday_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def birthday_list_command(message: types.Message):
    await handle_birthday_list_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа поздравь ") and message.from_user.id not in BLOCKED_USERS)
async def test_greeting_command(message: types.Message):
    await handle_test_greeting_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа все дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def admin_birthday_list_command(message: types.Message):
    await handle_admin_birthday_list_command(message)

# ================== БЛОК 6.8: ЧОБЫЛО И ИТОГИ ГОДА ==================
