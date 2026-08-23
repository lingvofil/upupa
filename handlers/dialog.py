"""Хэндлеры: Catch-all: диалог, реакции, мемы, статистика сообщений.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""

import logging

from aiogram import Router, types

import features.statistics as bot_statistics
from features.dialog_pipeline import process_dialog_pipeline
from games import crocodile, reverse_crocodile
from services import memegenerator


router = Router(name="dialog")


@router.message()
async def process_message(message: types.Message):
    # 1) Крокодил (обычный и наоборот): перехватываем только правильное угадывание
    if await crocodile.check_answer(message):
        return
    if await reverse_crocodile.check_answer(message):
        return

    # 2) Обычная обработка сообщений
    await memegenerator.check_and_send_random_meme(message)

    # 3) Единственный явный pipeline: реакции -> прямой диалог.
    if await process_dialog_pipeline(message):
        return

    try:
        if message.from_user:
            is_private = message.chat.type == "private"
            await bot_statistics.log_message(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                message_type=message.content_type,
                is_private=is_private,
                chat_title=message.chat.title if not is_private else None,
                user_name=message.from_user.full_name,
                user_username=message.from_user.username,
            )
    except Exception as exc:
        logging.error("Failed to log message stats: %s", exc)
