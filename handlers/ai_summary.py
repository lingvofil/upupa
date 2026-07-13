"""Хэндлеры: Чобыло, итоги года, туры, отели, билеты.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import random
from aiogram import F, types
from config import (
    ADMIN_ID, BLOCKED_USERS, model, LOG_FILE
)
from core.upupa_utils import normalize_upupa_command
from prompts import actions
from AI.summarize import summarize_chat_history, summarize_year
from AI.leveltravel import process_tours_command, process_hotels_command
from AI.tutu import process_tickets_command
from AI.chat_recall import (
    process_recall_command, process_verdict_command, process_factcheck_command
)
from AI.comic import process_comic_command
from services.news import process_tv_news_command, process_football_news_command

router = Router(name="ai_summary")


@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith(
    "упупа когда мы говорили"
) and message.from_user.id not in BLOCKED_USERS)
async def handle_recall(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await process_recall_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith(
    "упупа рассуди"
) and message.from_user.id not in BLOCKED_USERS)
async def handle_verdict(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await process_verdict_command(message)

@router.message(lambda message: message.text and message.text.lower().strip() in ("пиздиш", "пиздишь")
                and message.reply_to_message and message.from_user.id not in BLOCKED_USERS)
async def handle_factcheck(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await process_factcheck_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) in ("комикс", "упупа комикс")
                and message.from_user.id not in BLOCKED_USERS)
async def handle_comic(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    await process_comic_command(message)


@router.message(F.text.lower() == "чобыло")
async def handle_chobylo(message: types.Message):
    random_action = random.choice(actions)
    await summarize_chat_history(message, model, LOG_FILE, actions)

# Футбол регистрируется ДО общего обзора, чтобы "упупа новости футбола"
# не перехватывался триггером "упупа новости".
@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith(
    ("новости футбола", "упупа новости футбола", "футбольные новости", "упупа футбольные новости")
) and message.from_user.id not in BLOCKED_USERS)
async def handle_football_news(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await process_football_news_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith(
    ("чо по телеку", "что по телеку",
     "упупа чо по телеку", "упупа что по телеку", "упупа новости")
) and message.from_user.id not in BLOCKED_USERS)
async def handle_tv_news(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await process_tv_news_command(message)

@router.message(F.text.lower() == "итоги года", F.from_user.id == ADMIN_ID)
async def handle_year_results(message: types.Message):
    random_action = random.choice(actions)
    await summarize_year(message, model, LOG_FILE, actions)

# ================== БЛОК 6.9: LEVEL TRAVEL  ==================

@router.message(lambda message: 
    message.text and 
    message.text.lower().startswith("туры") and 
    message.from_user.id not in BLOCKED_USERS
)
async def handle_tours_command(message: types.Message):
    await process_tours_command(message)

@router.message(lambda message: 
    message.text and 
    message.text.lower().startswith("отели") and 
    message.from_user.id not in BLOCKED_USERS
)
async def handle_hotels_command(message: types.Message):
    await process_hotels_command(message)

# ================== БЛОК 6.10: TUTU АВИАБИЛЕТЫ  ==================

@router.message(lambda message: message.text and message.text.lower().startswith("билеты") and message.from_user.id not in BLOCKED_USERS)
async def handle_tickets_search(message: types.Message):
    await process_tickets_command(message)
       
# ================== БЛОК 6.11: ГОВОРИЛКА (ПРОМПТЫ, ДИАЛОГИ, СТИХИ) ==================
