"""Хэндлеры: Чотам, роботикс, опиши.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import random
from aiogram import Bot, Dispatcher, F, types
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from prompts import HELP_TEXT, actions, CHANNEL_SETTINGS, queries
from AI.adddescribe import (
    process_image_description,
    handle_add_text_command
)
from AI.whatisthere import (
    process_whatisthere_unified,
    get_processing_message,
    process_robotics_description
)

router = Router(name="ai_vision")


@router.message(lambda message: 
    (
        (
            (message.audio or message.voice or message.video or message.photo or 
             message.animation or message.sticker) and 
            message.caption and "чотам" in message.caption.lower()
        )
        or
        (
            message.text and "чотам" in message.text.lower() and 
            message.reply_to_message and 
            (message.reply_to_message.audio or message.reply_to_message.voice or 
             message.reply_to_message.video or message.reply_to_message.photo or 
             message.reply_to_message.animation or message.reply_to_message.sticker or
             message.reply_to_message.text)
        )
        or
        (
            message.text and "чотам" in message.text.lower() and 
            not message.reply_to_message
        )
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_whatisthere_unified(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    
    processing_text = get_processing_message(message)
    processing_msg = await message.reply(processing_text)
    
    success, response = await process_whatisthere_unified(message)
    await processing_msg.delete()
    await message.reply(response)

@router.message(lambda message: 
    (
        (
            (message.photo or message.video or message.animation) and 
            message.caption and "опиши сильно" in message.caption.lower()
        )
        or 
        (
            message.text and "опиши сильно" in message.text.lower() and 
            message.reply_to_message and 
            (message.reply_to_message.photo or message.reply_to_message.video or message.reply_to_message.animation)
        )
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_robotics_description(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing = await message.reply("Включаю модули анализа... (Robotics 1.5)")
    success, response = await process_robotics_description(message)
    await processing.delete()
    await message.reply(response)

@router.message(lambda message: 
    (
        (message.photo and message.caption and "опиши" in message.caption.lower())
        or 
        (message.text and "опиши" in message.text.lower() and message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document))
    ) and message.from_user.id not in BLOCKED_USERS
)
async def describe_image(message: types.Message):
    random_action = random.choice(actions)
    success, response = await process_image_description(bot, message)
    await message.reply(response)

# ================== БЛОК 6.6: ГЕНЕРАЦИЯ И РЕДАКТИРОВАНИЕ ИЗОБРАЖЕНИЙ ==================
