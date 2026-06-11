"""Хэндлеры: Статистика и лексикон.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import random
import logging
from aiogram import F, types
from aiogram.types import Message
from typing import Dict
from config import (
    ADMIN_ID
)
from prompts import actions
from features.stat_rank_settings import get_user_statistics, generate_chat_stats_report
from features.lexicon_settings import (
    process_my_lexicon, process_chat_lexicon, process_user_lexicon
)
import features.statistics as bot_statistics

router = Router(name="stats_lexicon")


def format_stats_message(stats: Dict[str, Dict], title: str) -> str:
    parts = [f"📊 *{title}*"]
    if stats.get("model_usage"):
        parts.append("\n🤖 *НАГРУЗКА НА GEMINI (Запросы):*")
        sorted_usage = sorted(stats["model_usage"].items(), key=lambda item: item[1], reverse=True)
        for chat_name, count in sorted_usage:
            parts.append(f"   🔥 `{chat_name}`: {count} запросов")
    else:
        parts.append("\n_Запросов к Gemini не зафиксировано._")

    if stats.get("groups"):
        parts.append("\n*Активность (Сообщения в чатах):*")
        sorted_groups = sorted(stats["groups"].items(), key=lambda item: item[1], reverse=True)
        for chat_title, count in sorted_groups:
            parts.append(f"   • `{chat_title}`: {count} сообщ.")
    else:
        parts.append("\n_Нет активности в групповых чатах._")

    if stats.get("private"):
        parts.append("\n*Личные сообщения:*")
        sorted_private = sorted(stats["private"].items(), key=lambda item: item[1], reverse=True)
        for user_display, count in sorted_private:
            parts.append(f"   • `{user_display}`: {count} сообщ.")
    else:
        parts.append("\n_Нет активности в личных сообщениях._")

    return "\n".join(parts)

@router.message(F.text.lower() == "стотистика", F.from_user.id == ADMIN_ID)
async def cmd_stats_total(message: Message):
    stats_data = await bot_statistics.get_total_messages()
    reply_text = format_stats_message(stats_data, "Общая статистика")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "стотистика сутки", F.from_user.id == ADMIN_ID)
async def cmd_stats_24h(message: Message):
    stats_data = await bot_statistics.get_messages_last_24_hours()
    reply_text = format_stats_message(stats_data, "Статистика за 24 часа")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "стотистика час", F.from_user.id == ADMIN_ID)
async def cmd_stats_1h(message: Message):
    stats_data = await bot_statistics.get_messages_last_hour()
    reply_text = format_stats_message(stats_data, "Статистика за час")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "моя статистика")
async def show_personal_stats(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    logging.info(f"Команда 'моя статистика' вызвана пользователем {message.from_user.id} в чате {message.chat.id}")
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    response, has_stats = await get_user_statistics(chat_id, user_id)
    await message.reply(response)

@router.message(F.text.lower() == "статистика чат")
async def show_chat_stats(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    report = await generate_chat_stats_report(str(message.chat.id))
    reply_text = report if report else "В этом чате нет корректных статистических данных."
    await message.reply(reply_text, parse_mode="HTML")

@router.message(lambda message: message.text and message.text.lower() == "мой лексикон")
async def handle_my_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    user_id = message.from_user.id
    chat_id = message.chat.id
    await process_my_lexicon(user_id, chat_id, message)

@router.message(lambda message: message.text and message.text.lower() == "лексикон чат")
async def handle_chat_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    response_text = await process_chat_lexicon(message)
    await message.reply(response_text)
    
@router.message(lambda message: message.text and message.text.lower().startswith("лексикон "))
async def handle_user_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    username_or_name = message.text[len("лексикон "):].strip()
    if username_or_name.startswith('@'):
        username_or_name = username_or_name[1:]       
    chat_id = message.chat.id
    await process_user_lexicon(username_or_name, chat_id, message)

# ================== БЛОК 5.6: ПОИСК И МЕДИА ==================
