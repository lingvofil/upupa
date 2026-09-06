"""Хэндлеры: Дни рождения.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import types
from core.settings import BLOCKED_USERS
from core.upupa_utils import normalize_upupa_command
from AI.birthday_calendar import (
    handle_birthday_command,
    handle_birthday_list_command,
    handle_test_greeting_command,
    handle_admin_birthday_list_command,
    parse_birthday_date,
)

router = Router(name="birthdays")


def _is_birthday_save_command(message: types.Message) -> bool:
    if not message.text:
        return False
    normalized = normalize_upupa_command(message.text)
    return (
        normalized.startswith("мой др ")
        or normalized.startswith("упупа запомни: мой др")
        or normalized.startswith("упупа запомни мой др")
    )


# ================== БЛОК 6.7: ДНИ РОЖДЕНИЯ ==================

@router.message(
    lambda message: _is_birthday_save_command(message)
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_birthday_save_command(message: types.Message):
    if parse_birthday_date(message.text or "") is None:
        await message.reply("Да пошел ты нахуй, пиши нормально: 'мой др 1 апреля'")
        return
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
