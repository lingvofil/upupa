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
from prompts import actions
from AI.summarize import summarize_chat_history, summarize_year
from AI.leveltravel import process_tours_command, process_hotels_command
from AI.tutu import process_tickets_command

router = Router(name="ai_summary")


@router.message(F.text.lower() == "чобыло")
async def handle_chobylo(message: types.Message):
    random_action = random.choice(actions)
    await summarize_chat_history(message, model, LOG_FILE, actions)

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
