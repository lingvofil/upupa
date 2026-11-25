"""Хэндлеры: Catch-all: диалог, реакции, мемы, статистика сообщений.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, or_f

import features.statistics as bot_statistics
from features.crocodile_scoring import check_regular_answer
from features.dialog_pipeline import process_dialog_pipeline
from features.dialogue_video import TEXT_COMMAND, send_dialogue_video
from games import crocodile_modes, reverse_crocodile, reverse_crocodile_guessing
from services import memegenerator


router = Router(name="dialog")


@router.message(or_f(F.text.regexp(TEXT_COMMAND), Command("dialog", "диалог", ignore_case=True)))
async def dialogue_video_command(message: types.Message):
    await send_dialogue_video(message)


@router.message()
async def process_message(message: types.Message):
    # 1) Крокодил: обычный, дуэль и обратные режимы.
    if await check_regular_answer(message):
        return
    if await crocodile_modes.check_duel_answer(message):
        return
    reverse_session = reverse_crocodile.games.get(str(message.chat.id)) or {}
    if reverse_session.get("mode") not in (None, "word"):
        if await reverse_crocodile_guessing.check_special_answer(message):
            return
    elif await reverse_crocodile.check_answer(message):
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
