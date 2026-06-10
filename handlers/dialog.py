"""Хэндлеры: Catch-all: диалог, реакции, мемы, статистика сообщений.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import logging
from aiogram import Bot, Dispatcher, F, types
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from features.chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, save_chat_settings, remove_chat
)
from features.stat_rank_settings import get_user_statistics, generate_chat_stats_report, track_message_statistics
from features.lexicon_settings import (
    process_my_lexicon, process_chat_lexicon, process_user_lexicon, save_user_message
)
import features.statistics as bot_statistics
from services import memegenerator
from games import crocodile
from AI.random_reactions import process_random_reactions
from AI.talking import (
    handle_list_prompts_command,
    handle_current_prompt_command,
    handle_set_prompt_command,
    handle_set_participant_prompt_command,
    handle_change_prompt_randomly_command,
    handle_poem_command,
    process_general_message,
    handle_switch_to_gigachat,
    handle_switch_to_gemini,
    handle_switch_to_groq,
    handle_which_model,
    handle_switch_to_history,
    handle_serious_mode_command,
    handle_serious_mode_reply,
    handle_switch_to_openrouter,
    handle_switch_to_siliconflow
)

router = Router(name="dialog")


@router.message()
async def process_message(message: types.Message):
    # 1) Крокодил: перехватываем только правильное угадывание
    if await crocodile.check_answer(message):
        return

    # 2. Обычная обработка сообщений
    await memegenerator.check_and_send_random_meme(message)
    
    # --- Обработка реакций и эмодзи ---
    should_stop = await process_random_reactions(
        message, model, save_user_message, track_message_statistics,
        add_chat, chat_settings, save_chat_settings
    )
    if should_stop:
        return

    await process_general_message(message)
    
    try:
        if message.from_user:
            is_private = message.chat.type == 'private'
            await bot_statistics.log_message(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                message_type=message.content_type,
                is_private=is_private,
                chat_title=message.chat.title if not is_private else None,
                user_name=message.from_user.full_name,
                user_username=message.from_user.username
            )
    except Exception as e:
        logging.error(f"Failed to log message stats: {e}")
    
