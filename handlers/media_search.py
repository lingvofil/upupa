"""Хэндлеры: Каналы, поиск, шерлок, котогиф, имена, дисторшн, рассылки, погода.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import random
from aiogram import types
from aiogram.types import Message
from config import (
    BLOCKED_USERS
)
from core.upupa_utils import normalize_upupa_command
from prompts import actions, CHANNEL_SETTINGS, queries
from features.channels_settings import process_channel_command
from services.search import (
    handle_message,
    process_image_search,
    save_and_send_searched_image,
    process_gif_search,
    save_and_send_gif   
)
from services.sherlock import is_sherlock_command, process_sherlock_command
from services.weather import (
    handle_current_weather_command, 
    handle_weekly_forecast_command
)
from services.nameinfo import process_name_info
from services.distortion import is_distortion_command, handle_distortion_request
from features.broadcast import handle_broadcast_command, is_broadcast_command

router = Router(name="media_search")


@router.message(lambda message: message.text and message.text.lower() in CHANNEL_SETTINGS.keys())
async def send_random_media(message: types.Message):
    await process_channel_command(message, CHANNEL_SETTINGS)

@router.message(lambda message: message.text and message.text.lower().startswith("найди") and message.from_user.id not in BLOCKED_USERS)
async def handle_image_search(message: Message):
    random_action = random.choice(actions)
    query = message.text[len("найди"):].strip()
    success, response_message, image_data = await process_image_search(query)
    if success and image_data:
        await save_and_send_searched_image(message, image_data)
    elif response_message:
        await message.reply(response_message)

@router.message(lambda message: is_sherlock_command(message.text) and message.from_user.id not in BLOCKED_USERS)
async def handle_sherlock_search(message: Message):
    await process_sherlock_command(message)

@router.message(lambda message: message.text and message.text.lower() in queries and message.from_user.id not in BLOCKED_USERS)
async def universal_handler(message: types.Message):
    keyword = message.text.lower()
    query, temp_img_path, error_msg = queries[keyword]
    await handle_message(message, query, temp_img_path, error_msg)

@router.message(lambda message: message.text and (
    message.text.lower() == "гиф" or message.text.lower().startswith("гиф ")
) and message.from_user.id not in BLOCKED_USERS)
async def handle_gif_search(message: types.Message):
    query = message.text[len("гиф"):].strip()
    if not query:
        await message.reply("Гиф чего? Пиши: гиф [запрос], например: гиф кот")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    success, error_message, gif_data = await process_gif_search(query)
    if success and gif_data:
        await save_and_send_gif(message, gif_data)
    elif error_message:
        await message.reply(error_message)

@router.message(lambda message: message.text and message.text.lower().replace(" ", "") == "котогиф" and message.from_user.id not in BLOCKED_USERS)
async def send_kotogif(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    await message.reply("Ща ща")
    success, error_message, gif_data = await process_gif_search("cat")
    if success and gif_data:
        await save_and_send_gif(message, gif_data)
    elif error_message:
        await message.reply(error_message)

# ================== БЛОК 5.7: РАЗНОЕ ==================

@router.message(lambda message: 
    message.text and 
    message.text.lower().startswith("имя ") and 
    message.from_user and
    message.from_user.id not in BLOCKED_USERS
)
async def handle_name_info(message: types.Message):
    random_action = random.choice(actions)
    success, response = await process_name_info(message)
    await message.reply(response)

@router.message(is_distortion_command)
async def handle_distortion_command(message: types.Message):
    await handle_distortion_request(message)

@router.message(lambda message: message.text and is_broadcast_command(message.text) and message.from_user.id not in BLOCKED_USERS)
async def handle_broadcast(message: types.Message):
    await handle_broadcast_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа погода" and message.from_user.id not in BLOCKED_USERS)
async def handle_weather_command(message: types.Message):
    await handle_current_weather_command(message)
        
@router.message(lambda message: message.text and message.text.lower().startswith("погода неделя") and message.from_user.id not in BLOCKED_USERS)
async def handle_weekly_forecast(message: types.Message):
    await handle_weekly_forecast_command(message)
