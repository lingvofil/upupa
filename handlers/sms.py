"""Хэндлеры: СМС и ММС.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import Bot, Dispatcher, F, types
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from features.sms_settings import (
    process_disable_sms, process_enable_sms,
    process_send_sms, process_send_mms, process_what_they_say
)

router = Router(name="sms")


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
    await process_send_sms(message, chat_list, bot)

@router.message(lambda message: (message.text and message.text.lower().startswith("ммс ")) or 
                                 (message.caption and message.caption.lower().startswith("ммс ")))
async def handle_send_mms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    await process_send_mms(message, chat_list, bot)

#@router.message(lambda message: message.text and message.text.lower().split(maxsplit=1)[0] == "чоговорят")
#async def handle_what_they_say(message: types.Message):
    #await process_what_they_say(message, chat_list, bot)

# ================== БЛОК 5.5: СТАТИСТИКА И ЛЕКСИКОН ==================
