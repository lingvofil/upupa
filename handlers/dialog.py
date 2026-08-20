"""Хэндлеры: Catch-all: диалог, реакции, мемы, статистика сообщений.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import logging
from aiogram import types
from config import (
    model, chat_settings
)
from features.chat_settings import (
    add_chat, save_chat_settings
)
from features.stat_rank_settings import track_message_statistics
from features.lexicon_settings import (
    save_user_message
)
import features.statistics as bot_statistics
from services import memegenerator
from games import crocodile, reverse_crocodile
import AI.random_reactions as random_reactions
from AI.situational_summary import install_into_random_reactions
import AI.talking as talking

# Старый random_reactions.py большой и содержит много независимых реакций.
# Меняем ситуативную вставку и добавляем защиту от двойной обработки одного message_id.
install_into_random_reactions(random_reactions)
process_random_reactions = random_reactions.process_random_reactions

# AI.talking исторически импортирует process_random_reactions напрямую и затем вызывает его
# ещё раз внутри process_general_message. Привязываем туда тот же идемпотентный wrapper,
# чтобы второй проход не повторял реакции, сохранение сообщений и статистику.
talking.process_random_reactions = process_random_reactions
process_general_message = talking.process_general_message

router = Router(name="dialog")


@router.message()
async def process_message(message: types.Message):
    # 1) Крокодил (обычный и наоборот): перехватываем только правильное угадывание
    if await crocodile.check_answer(message):
        return
    if await reverse_crocodile.check_answer(message):
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
    
