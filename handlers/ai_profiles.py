"""Хэндлеры: Кто я, что за чат, пародия, викторина, кем стать, упупа скажи.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import random
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile, Message, PollAnswer, BufferedInputFile
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from core.upupa_utils import normalize_upupa_command
from prompts import HELP_TEXT, actions, CHANNEL_SETTINGS, queries
from AI.whoparody import (
    process_user_profile,
    process_chat_profile,
    process_parody
)
from AI.quiz import process_quiz_start, process_poll_answer, schedule_daily_quiz, process_participant_quiz_start
from AI.profession import get_random_okved_and_commentary 
from AI.voice import handle_voice_command

router = Router(name="ai_profiles")


@router.message(lambda message: message.text and message.text.lower() == "что за чат")
async def handle_chat_profile(message: types.Message):
    await process_chat_profile(message)

@router.message(lambda message: message.text and message.text.lower() == "кто я")
async def handle_user_profile(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    user_id = message.from_user.id
    chat_id = message.chat.id
    await process_user_profile(user_id, chat_id, message)

@router.message(lambda message: message.text and message.text.lower().startswith("пародия"))
async def handle_parody(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    chat_id = message.chat.id
    await process_parody(message, chat_id)

# ================== БЛОК 6.3: ВИКТОРИНЫ И ПРОФЕССИИ ==================

@router.message(F.text.lower() == "викторина участники")
async def start_participant_quiz(message: Message, bot: Bot):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("ищем цитаты великих людей...")
    success, error_message = await process_participant_quiz_start(message, bot)
    await processing_msg.delete()
    if not success:
        await message.reply(error_message)

@router.message(F.text.lower().contains("викторина"))
async def start_quiz(message: Message, bot: Bot):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую вапросики...")
    success, error_message = await process_quiz_start(message, bot)
    await processing_msg.delete()
    if not success:
        await message.reply(error_message)

@router.message(F.text.lower() == "кем стать") 
async def choose_profession_command(message: types.Message):
    await get_random_okved_and_commentary(message)

# ================== БЛОК 6.4: ГОЛОС ==================

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа скажи") and message.from_user.id not in BLOCKED_USERS)
async def handle_voice_msg_cmd(message: Message):
    await handle_voice_command(message, bot)

# ================== БЛОК 6.5: ЧОТАМ И ОПИСАНИЯ ==================
