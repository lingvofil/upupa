#main.py

# ================== БЛОК 0: Библиотеки ==================
import os
import random
import logging
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile, Message, PollAnswer, BufferedInputFile
from aiogram.filters import CommandStart, Filter
from aiogram.filters.command import Command
import json
from datetime import datetime, timedelta
import re
from typing import Dict
import requests

# ================== БЛОК 1: Конфигурация ==================
from config import (
    bot, dp, router, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)

# Импорт функции нормализации команд с "упупа"
from upupa_utils import normalize_upupa_command

# ================== БЛОК 2: СПРАВКА, ПРОМПТЫ, РАНГИ, СТОП-СЛОВА, КАНАЛЫ, ЖИВОТНЫЕ ==================
from prompts import HELP_TEXT, actions, CHANNEL_SETTINGS, queries

# ================== БЛОК 3: НАСТРОЙКИ БЕЗ AI ==================

# ================== БЛОК 3.1: ОБЩИЕ НАСТРОЙКИ ==================
from common_settings import process_leave_chat, process_leave_empty_chats
        
# ================== БЛОК 3.2: НАСТРОЙКА ЧАТОВ ==================
from chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, save_chat_settings
)

# ================== БЛОК 3.3: НАСТРОЙКА СТАТИСТИКИ, РАНГОВ ==================
from stat_rank_settings import get_user_statistics, generate_chat_stats_report, track_message_statistics

# ================== БЛОК 3.4: НАСТРОЙКА ЛЕКСИКОНА ==================
from lexicon_settings import (
    process_my_lexicon, process_chat_lexicon, process_user_lexicon, save_user_message
)

# ================== БЛОК 3.5: НАСТРОЙКА СМС, ММС ==================
from sms_settings import (
    process_disable_sms, process_enable_sms,
    process_send_sms, process_send_mms
)

# ================== БЛОК 3.6: НАСТРОЙКА ПЕРЕСЫЛКИ МЕДИА ==================
from channels_settings import process_channel_command

# ================== БЛОК 3.7: НАСТРОЙКА ПОИСКА ==================
from search import (
    handle_message,
    process_image_search,
    save_and_send_searched_image,
    process_gif_search,
    save_and_send_gif   
)
from sherlock import is_sherlock_command, process_sherlock_command

# ================== БЛОК 3.8: НАСТРОЙКА ПОГОДЫ ==================
from weather import (
    handle_current_weather_command, 
    handle_weekly_forecast_command
)

# ================== БЛОК 3.9: НАСТРОЙКА ИМЕНИ ==================
from nameinfo import process_name_info

# ================== БЛОК 3.10 ПРЕКОЛЬНАЯ ЕГРА ==================
from egra import start_egra, handle_egra_answer, handle_final_button_press

# ================== БЛОК 3.11: НАСТРОЙКА РАСЧЕТА НАГРУЗКИ БОТА ==================
import statistics as bot_statistics
from statistics import PrivateRateLimitMiddleware
from middlewares import IncomingMessageLogMiddleware

# ================== БЛОК 3.12: НАСТРОЙКИ ДИСТОРШН ==================
from distortion import is_distortion_command, handle_distortion_request

# ================== БЛОК 3.13 РАССЫЛКИ ==================
from broadcast import handle_broadcast_command, is_broadcast_command

# ================== БЛОК 3.14: ИНТЕРАКТИВНЫЕ НАСТРОЙКИ ==================
from interactive_settings import send_settings_menu, handle_settings_callback, send_help_menu, handle_help_callback

# ================== БЛОК 3.15 КОНТЕНТ-ФИЛЬТРА ==================
from content_filter import ContentFilterMiddleware

# ================== БЛОК 3.16 МЕМЫ ==================
import memegenerator

# ================== БЛОК 3.17 КРОКОДИЛ ==================
import crocodile

# ================== БЛОК 3.18 СЛУЧАЙНЫЕ РЕАКЦИИ ==================
from AI.random_reactions import process_random_reactions

# ================== БЛОК 3.19: ПУП (YTP) ==================
from ytp import handle_ytp_command


# ================== БЛОК 4: НАСТРОЙКИ AI ==================

# ================== БЛОК 4.1: КТО Я, ЧТО ЗА ЧАТ, ПАРОДИЯ ==================
from AI.whoparody import (
    process_user_profile,
    process_chat_profile,
    process_parody
)

# ================== БЛОК 4.2: ВИКТОРИНА ==================
from AI.quiz import process_quiz_start, process_poll_answer, schedule_daily_quiz, process_participant_quiz_start

# ================== БЛОК 4.3: ДОБАВЬ ОПИШИ ==================
from AI.adddescribe import (
    process_image_description,
    handle_add_text_command
)

# ================== БЛОК 4.4: ЧОТАМ ==================
from AI.whatisthere import (
    process_whatisthere_unified,
    get_processing_message,
    process_robotics_description
)

# ================== БЛОК 4.5: ГЕНЕРАЦИЯ КАРТИНОК ==================
from AI.picgeneration import (
    handle_pun_image_command,
    handle_image_generation_command,
    handle_redraw_command,
    handle_edit_command,
    handle_kandinsky_generation_command,
    handle_nvidia_command
)

# ================== БЛОК 4.6: ГОВОРИЛКА ==================
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
     handle_serious_mode_reply
)

# ================== БЛОК 4.7: ЧОБЫЛО И ИТОГИ ГОДА ==================
from AI.summarize import summarize_chat_history, summarize_year

# ================== БЛОК 4.8: ПРОФЕССИИ ==================
from AI.profession import get_random_okved_and_commentary 

# ================== БЛОК 4.9: КАЛЕНДАРЬ ДНЕЙ РОЖДЕНИЯ ==================
from AI.birthday_calendar import (
    handle_birthday_command,
    handle_birthday_list_command,
    handle_test_greeting_command,
    handle_admin_birthday_list_command,
    birthday_scheduler
)

# ================== БЛОК 4.10 ДНД ==================
from AI.dnd import dnd_router 

# ================== БЛОК 4.11 ГОЛОСОВОЙ МОДУЛЬ ==================
from AI.voice import handle_voice_command

# ================== БЛОК 4.12 LEVEL TRAVEL ==================
from AI.leveltravel import process_tours_command, process_hotels_command

# ================== БЛОК 4.13 TUTU АВИАБИЛЕТЫ ==================
from AI.tutu import process_tickets_command

# ================== БЛОК 5: ХЭНДЛЕРЫ БЕЗ AI ==================

router.message.middleware(IncomingMessageLogMiddleware())
router.message.middleware(ContentFilterMiddleware())
router.message.middleware(PrivateRateLimitMiddleware())

# ================== БЛОК 5.1: БАЗОВЫЕ КОМАНДЫ ==================

@router.message(CommandStart())
async def process_start_command(message: types.Message):
    await message.reply("Я пидорас")

@router.message(lambda message: message.text is not None and message.text.lower() == "очистка" and message.from_user.id not in BLOCKED_USERS)
async def process_clear_command(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in conversation_history:
        conversation_history[chat_id] = []
        await message.reply("Смыто всё говно")
    else:
        await message.reply("История и так пустая, долбоёб")

# ================== БЛОК 5.2: СПРАВКА И НАСТРОЙКИ ==================

@router.message(lambda message: message.text and message.text.lower() in ["чоумееш", "справка", "help", "помощь"] and message.from_user.id not in BLOCKED_USERS)
async def handle_chooumeesh(message: types.Message):
    await send_help_menu(message)
    
@router.callback_query(F.data.startswith("help:"))
async def help_callback_handler(query: types.CallbackQuery):
    await handle_help_callback(query)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа настройки")
async def settings_command_handler(message: types.Message):
    await send_settings_menu(message)

@router.callback_query(F.data.startswith("settings:"))
async def settings_callback_handler(query: types.CallbackQuery):
    await handle_settings_callback(query)

# ================== БЛОК 5.3: УПРАВЛЕНИЕ ЧАТАМИ ==================

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа выйди из чатов хуесосов")
async def leave_empty_chats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    await process_leave_empty_chats(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа выйди из "))
async def leave_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    # Извлекаем идентификатор чата из нормализованного текста
    normalized = normalize_upupa_command(message.text)
    chat_identifier = normalized[len("упупа выйди из "):].strip()
    await process_leave_chat(message, chat_identifier)
    
@router.message(lambda message: message.text and message.text.lower() == "обновить чаты")
async def update_all_chats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Иди нахуй, у тебя нет прав на это.")
        return
    await process_update_all_chats(message, bot)

@router.message(lambda message: message.text and message.text.lower() == "где сидишь")
async def handle_where_sits(message: types.Message):
    response = get_chats_list(message.chat.id, message.chat.title, message.chat.username)
    await message.reply(response)

# ================== БЛОК 5.4: СМС И ММС ==================

@router.message(lambda message: message.text and message.text.lower() == "отключи смс")
async def disable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    response = await process_disable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower() == "включи смс")
async def enable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    response = await process_enable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower().startswith("смс "))
async def handle_send_sms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    await process_send_sms(message, chat_list, bot)

@router.message(lambda message: (message.text and message.text.lower().startswith("ммс ")) or 
                                 (message.caption and message.caption.lower().startswith("ммс ")))
async def handle_send_mms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    await process_send_mms(message, chat_list, bot)

# ================== БЛОК 5.5: СТАТИСТИКА И ЛЕКСИКОН ==================

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

@router.message(F.text.lower() == "егра")
async def egra_command_handler(message: types.Message):
    await start_egra(message, bot)

@router.poll_answer()
async def handle_poll_answers(poll_answer: PollAnswer, bot: Bot):
    is_egra_handled = await handle_egra_answer(poll_answer, bot)
    if not is_egra_handled:
        await process_poll_answer(poll_answer, bot)

@router.callback_query(F.data == "egra_final_choice")
async def egra_callback_handler(callback_query: types.CallbackQuery):
    await handle_final_button_press(callback_query, bot)

@router.message(F.text.lower().in_(["мем", "meme"]))
async def meme_command_handler(message: Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    reply_text = message.reply_to_message.text if message.reply_to_message else None
    photo = await memegenerator.create_meme_image(message.chat.id, reply_text)
    if photo:
        await message.answer_photo(photo)
    else:
        await message.answer("Ошибка при создании мема.")

@router.message(F.text.lower() == "кракадил")
async def start_croc(message: types.Message):
    print("CROC BOT ID:", id(bot))
    await crocodile.handle_start_game(message)

@router.callback_query(F.data.startswith("cr_"))
async def croc_callback(callback: types.CallbackQuery):
    if callback.data == "cr_restart":
        await crocodile.handle_start_game(callback.message)
        await callback.answer()
    else:
        await crocodile.handle_callback(callback)

@router.message(lambda m: m.text and m.text.lower().strip() == "кракадил стоп")
async def stop_croc_text(message: types.Message):
    await crocodile.handle_text_stop(message)


def is_video_document(msg: types.Message) -> bool:
    if not msg or not msg.document:
        return False
    if msg.document.mime_type and msg.document.mime_type.startswith("video/"):
        return True
    return False


@router.message(
    lambda message: (
        (
            message.text and
            message.text.lower().strip() == "пуп" and
            message.reply_to_message and
            (
                message.reply_to_message.video
                or message.reply_to_message.animation
                or (message.reply_to_message.sticker and message.reply_to_message.sticker.is_video)
                or is_video_document(message.reply_to_message)
            )
        )
        or
        (
            (
                message.video
                or message.animation
                or (message.sticker and message.sticker.is_video)
                or is_video_document(message)
            ) and
            message.caption and
            message.caption.lower().strip() == "пуп"
        )
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_pup_command(message: types.Message):
    await handle_ytp_command(message, bot)

# ================== БЛОК 6: ХЭНДЛЕРЫ С AI ==================

# ================== БЛОК 6.1: ПЕРЕКЛЮЧЕНИЕ МОДЕЛЕЙ ==================

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа гигачат")
async def switch_to_gigachat(message: types.Message):
    await handle_switch_to_gigachat(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа гемини")
async def switch_to_gemini(message: types.Message):
    await handle_switch_to_gemini(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа грок")
async def switch_to_groq(message: types.Message):
    await handle_switch_to_groq(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа нушо")
async def cmd_switch_history(message: Message):
    await handle_switch_to_history(message)

@router.message(F.text.lower() == "какая модель")
async def which_model(message: types.Message):
    await handle_which_model(message)

# ================== БЛОК 6.2: ПРОФИЛИ И ПАРОДИЯ ==================

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

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "отредактируй" in message.caption.lower()) or
            (message.document and message.caption and "отредактируй" in message.caption.lower()) or
            (message.text and "отредактируй" in message.text.lower() and message.reply_to_message and 
            (message.reply_to_message.photo or message.reply_to_message.document))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def edit_image(message: types.Message):
    await handle_edit_command(message)

@router.message(
    lambda message: (
        message.from_user.id not in BLOCKED_USERS and
        message.text and (
            message.text.lower().startswith("нарисуй") or
            (message.text.lower().strip() == "нарисуй" and message.reply_to_message)
        )
    )
)
async def generate_image(message: types.Message):
    await handle_image_generation_command(message)
    
@router.message(
    lambda message: (
        message.from_user.id not in BLOCKED_USERS and
        message.text and (
            message.text.lower().startswith("сгенерируй") or
            (message.text.lower().strip() == "сгенерируй" and message.reply_to_message)
        )
    )
)
async def generate_image_kandinsky(message: types.Message):
    await handle_kandinsky_generation_command(message)

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "перерисуй" in message.caption.lower()) or
            (message.document and message.caption and "перерисуй" in message.caption.lower()) or
            (message.text and "перерисуй" in message.text.lower() and message.reply_to_message and 
            (message.reply_to_message.photo or message.reply_to_message.document))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def redraw_image(message: types.Message):
    await handle_redraw_command(message)

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "нвидиа" in message.caption.lower()) or
            (message.document and message.caption and "нвидиа" in message.caption.lower()) or
            (message.text and "нвидиа" in message.text.lower() and message.reply_to_message and 
            (
                message.reply_to_message.photo or 
                message.reply_to_message.document or
                (message.reply_to_message.sticker and 
                 not message.reply_to_message.sticker.is_animated and 
                 not message.reply_to_message.sticker.is_video)
            ))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def nvidia_image(message: types.Message):
    await handle_nvidia_command(message)

@router.message(
    lambda message: message.text and 
    message.text.lower().strip() == "скаламбурь" and 
    message.from_user.id not in BLOCKED_USERS
)
async def generate_pun_with_image(message: types.Message):
    await handle_pun_image_command(message)
    
@router.message(
    lambda message: (
        (message.photo and message.caption and "добавь" in message.caption.lower()) or 
        (message.text and "добавь" in message.text.lower() and message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document))
    ) and message.from_user.id not in BLOCKED_USERS
)
async def add_text_to_image(message: types.Message):
    await handle_add_text_command(message)

# ================== БЛОК 6.7: ДНИ РОЖДЕНИЯ ==================
    
@router.message(lambda message: message.text and 
                (normalize_upupa_command(message.text).startswith("упупа запомни: мой др") or 
                 normalize_upupa_command(message.text).startswith("упупа запомни мой др")) and 
                 message.from_user.id not in BLOCKED_USERS)
async def handle_birthday_save_command(message: types.Message):
    await handle_birthday_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def birthday_list_command(message: types.Message):
    await handle_birthday_list_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа поздравь ") and message.from_user.id not in BLOCKED_USERS)
async def test_greeting_command(message: types.Message):
    await handle_test_greeting_command(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа все дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def admin_birthday_list_command(message: types.Message):
    await handle_admin_birthday_list_command(message)

# ================== БЛОК 6.8: ЧОБЫЛО И ИТОГИ ГОДА ==================

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

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа не болтай")
async def disable_dialog(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
    chat_settings[chat_id]["dialog_enabled"] = False
    save_chat_settings()
    await message.reply("Лана отъебитесь.")

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа говори")
async def enable_dialog(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
    chat_settings[chat_id]["dialog_enabled"] = True
    save_chat_settings()
    await message.reply("Дратути")

@router.message(F.text.lower() == "промпты")
async def list_prompts_command(message: types.Message):
    await handle_list_prompts_command(message)

@router.message(F.text.lower() == "какой промпт")
async def current_prompt_command(message: types.Message):
    await handle_current_prompt_command(message)

@router.message(F.text.lower().startswith("промпт участник "))
async def set_participant_prompt_command(message: types.Message):
    await handle_set_participant_prompt_command(message)

@router.message(F.text.lower().startswith("промпт "))
async def set_prompt_command(message: types.Message):
    await handle_set_prompt_command(message)

@router.message(F.text.lower() == "поменяй промпт")
async def change_prompt_randomly_command(message: types.Message):
    await handle_change_prompt_randomly_command(message)

@router.message(lambda message: message.text and message.text.lower().startswith(("пирожок", "порошок")))
async def handle_poem(message: types.Message):
    poem_type = "пирожок" if message.text.lower().startswith("пирожок") else "порошок"
    await handle_poem_command(message, poem_type)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа умоляю"))
async def serious_mode_command(message: types.Message):
    await handle_serious_mode_command(message)

# ================== БЛОК 6.12: ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ==================
        
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
    
# ================== БЛОК 7: ЗАПУСК БОТА ==================
async def main():
    # --- антиспам ---
    from content_filter import load_antispam_settings
    load_antispam_settings()

    # --- статистика ---
    bot_statistics.init_db()

    # --- планировщики викторин ---
    chat_ids = ['-1001707530786', '-1001781970364']
    for chat_id in chat_ids:
        asyncio.create_task(
            schedule_daily_quiz(bot, int(chat_id))
        )

    # --- планировщик дней рождения ---
    asyncio.create_task(
        birthday_scheduler(bot)
    )

    # --- КРОКОДИЛ: socket.io сервер ---
    # ВАЖНО: только create_task, без await
    asyncio.create_task(
        crocodile.start_socket_server()
    )

    # --- роутеры ---
    dp.include_router(dnd_router)
    dp.include_router(router)

    # --- HTTP-сессия бота ---
    bot.session = AiohttpSession(timeout=60)

    # --- polling ---
    # webhook гарантированно выключаем
    await bot.delete_webhook(drop_pending_updates=True)

    # стартуем polling (БЛОКИРУЮЩИЙ)
    print("MAIN BOT ID:", id(bot))
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
