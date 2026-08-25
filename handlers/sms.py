"""Хэндлеры: СМС и ММС.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
import logging

from aiogram import Router

from aiogram import types
from core.loader import bot
from core.state import chat_list, sms_disabled_chats
from features.sms_settings import (
    _get_numbered_chats,
    process_disable_sms, process_enable_sms,
    process_send_sms, process_send_mms
)

router = Router(name="sms")


async def _reject_channel_target(message: types.Message) -> bool:
    """Не даёт использовать межчатовые СМС/ММС для публикации в каналы."""
    command_text = message.text or message.caption or ""
    parts = command_text.split(maxsplit=2)
    if len(parts) < 2:
        return False

    try:
        chat_index = int(parts[1]) - 1
    except ValueError:
        return False

    filtered_chats = _get_numbered_chats(chat_list)
    if chat_index < 0 or chat_index >= len(filtered_chats):
        return False

    target_chat_id = filtered_chats[chat_index]["id"]
    try:
        target_chat = await bot.get_chat(target_chat_id)
    except Exception as e:
        logging.warning("Не удалось проверить тип чата %s перед СМС/ММС: %s", target_chat_id, e)
        await message.reply("Не могу проверить тип адресата, поэтому СМС/ММС не отправляю.")
        return True

    if getattr(target_chat, "type", None) != "channel":
        return False

    await message.reply("В каналы СМС и ММС не отправляю.")
    return True


@router.message(lambda message: message.text and message.text.lower() == "отключи смс")
async def disable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    response = await process_disable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower() == "включи смс")
async def enable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    response = await process_enable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower().startswith("смс "))
async def handle_send_sms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    if await _reject_channel_target(message):
        return
    await process_send_sms(message, chat_list, bot)

@router.message(lambda message: (message.text and message.text.lower().startswith("ммс ")) or 
                                 (message.caption and message.caption.lower().startswith("ммс ")))
async def handle_send_mms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    if await _reject_channel_target(message):
        return
    await process_send_mms(message, chat_list, bot)

#@router.message(lambda message: message.text and message.text.lower().split(maxsplit=1)[0] == "чоговорят")
#async def handle_what_they_say(message: types.Message):
    #await process_what_they_say(message, chat_list, bot)

# ================== БЛОК 5.5: СТАТИСТИКА И ЛЕКСИКОН ==================
