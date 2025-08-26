# ================== БЛОК 0: Библиотеки ==================
import os
import random
import logging
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile, Message, PollAnswer, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.filters.command import Command
import json
import nest_asyncio
from datetime import datetime, timedelta
import re
from typing import Dict

nest_asyncio.apply()
# ================== БЛОК 1: Конфигурация ==================
from config import (
    bot, dp, router, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)

# ================== БЛОК 2: СПРАВКА, ПРОМПТЫ, РАНГИ, СТОП-СЛОВА, КАНАЛЫ, ЖИВОТНЫЕ ==================
from prompts import HELP_TEXT, actions, CHANNEL_SETTINGS, queries

# ================== БЛОК 3.1: ОБЩИЕ НАСТРОЙКИ ==================
from common_settings import process_leave_chat
        
# ================== БЛОК 3.2: НАСТРОЙКА ЧАТОВ ==================
from chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, save_chat_settings
)

# ================== БЛОК 3.3: НАСТРОЙКА СТАТИСТИКИ, РАНГОВ ==================
from stat_rank_settings import get_user_statistics, generate_chat_stats_report

# ================== БЛОК 3.4: НАСТРОЙКА ЛЕКСИКОНА ==================
from lexicon_settings import (
    process_my_lexicon, process_chat_lexicon, process_user_lexicon
)

# ================== БЛОК 3.5: НАСТРОЙКА СМС, ММС ==================
from sms_settings import (
    process_disable_sms, process_enable_sms,
    process_send_sms, process_send_mms
)

# ================== БЛОК 3.6: НАСТРОЙКА КТО Я, ЧТО ЗА ЧАТ, ПАРОДИЯ ==================
from whoparody import (
    process_user_profile,
    process_chat_profile,
    process_parody
)

# ================== БЛОК 3.7: НАСТРОЙКА ВИКТОРИНА ==================
from quiz import process_quiz_start, process_poll_answer, schedule_daily_quiz, process_participant_quiz_start

# ================== БЛОК 3.8: НАСТРОЙКА ДОБАВЬ ОПИШИ ==================
from adddescribe import (
    process_image_description,
    handle_add_text_command
)

# ================== БЛОК 3.9: НАСТРОЙКА ЧОТАМ ==================
from whatisthere import (
    process_whatisthere_unified,
    get_processing_message
)

# ================== БЛОК 3.7: НАСТРОЙКА ПЕРЕСЫЛКИ МЕДИА ==================
from channels_settings import process_channel_command

# ================== БЛОК 3.8: НАСТРОЙКА ПОИСКА ==================
from search import (
    handle_message,
    process_image_search,
    save_and_send_searched_image,
    process_gif_search,
    save_and_send_gif
)

# ================== БЛОК 3.9: НАСТРОЙКА ГЕНЕРАЦИИ КАРТИНОК ==================
from picgeneration import handle_generate_command
from gemini_generation import handle_draw_command, handle_redraw_command, handle_pun_image_command

# ================== БЛОК 3.10: НАСТРОЙКА ПОГОДЫ ==================
from weather import (
    handle_current_weather_command, 
    handle_weekly_forecast_command
)
# ================== БЛОК 3.11: НАСТРОЙКА ГОВОРИЛКИ ==================
from talking import (
    handle_list_prompts_command,
    handle_current_prompt_command,
    handle_set_prompt_command,
    handle_set_participant_prompt_command,
    handle_change_prompt_randomly_command,
    handle_poem_command,
    process_general_message
)
from random_reactions import process_random_reactions

# ================== БЛОК 3.15: НАСТРОЙКА ИМЕНИ ==================
from nameinfo import process_name_info

# ================== БЛОК 3.16: НАСТРОЙКА ЧОБЫЛО ==================
from summarize import summarize_chat_history

# ================== БЛОК 3.17 ПРЕКОЛЬНАЯ ЕГРА ==================
from egra import start_egra, handle_egra_answer, handle_final_button_press

# ================== БЛОК 3.18: НАСТРОЙКА ПРОФЕССИЙ ==================
from profession import get_random_okved_and_commentary 

# ================== БЛОК 3.18: НАСТРОЙКА РАСЧЕТА НАГРУЗКИ БОТА ==================
import statistics as bot_statistics
from statistics import PrivateRateLimitMiddleware

# ================== БЛОК 3.19: КАЛЕНДАРЬ ДНЕЙ РОЖДЕНИЯ ==================
from birthday_calendar import (
    handle_birthday_command,
    handle_birthday_list_command,
    handle_test_greeting_command,
    handle_admin_birthday_list_command,
    birthday_scheduler
)

# ================== БЛОК 3.20: НАСТРОЙКИ ДИСТОРШН ==================
from distortion import is_distortion_command, handle_distortion_request

# ================== БЛОК РАССЫЛКИ ==================
from broadcast import handle_broadcast_command, is_broadcast_command

# ================== БЛОК 3.21: ИНТЕРАКТИВНЫЕ НАСТРОЙКИ (НОВОЕ) ==================
from interactive_settings import send_settings_menu, handle_settings_callback
        
# ================== БЛОК 4: ХЭНДЛЕРЫ ==================
router.message.middleware(PrivateRateLimitMiddleware())
def format_stats_message(stats: Dict[str, Dict], title: str) -> str:
    """Вспомогательная функция для красивого форматирования статистики."""
    parts = [f"📊 *{title}*"]

    if stats.get("groups"):
        parts.append("\n*Чаты:*")
        sorted_groups = sorted(stats["groups"].items(), key=lambda item: item[1], reverse=True)
        for chat_title, count in sorted_groups:
            parts.append(f"  • `{chat_title}`: {count} сообщ.")
    else:
        parts.append("\n_Нет активности в групповых чатах._")

    if stats.get("private"):
        parts.append("\n*Личные сообщения:*")
        sorted_private = sorted(stats["private"].items(), key=lambda item: item[1], reverse=True)
        for user_display, count in sorted_private:
            parts.append(f"  • `{user_display}`: {count} сообщ.")
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

@router.message(CommandStart())
async def process_start_command(message: types.Message):
    await message.reply("Я пидорас")

@router.message(lambda message: message.text is not None and message.text.lower() == "очистка" and message.from_user.id not in BLOCKED_USERS)
async def process_clear_command(message: types.Message):
    user_id = message.from_user.id
    conversation_history[user_id] = []
    await message.reply("Смыто всё говно")

@router.message(lambda message: message.text and message.text.lower() == "чоумееш" and message.from_user.id not in BLOCKED_USERS)
async def handle_chooumeesh(message: types.Message):
    await message.reply(HELP_TEXT)
    
@router.message(lambda message: message.text and is_broadcast_command(message.text) and message.from_user.id not in BLOCKED_USERS)
async def handle_broadcast(message: types.Message):
    await handle_broadcast_command(message)

# ================== НАЧАЛО БЛОКА ИНТЕРАКТИВНЫХ НАСТРОЕК (НОВОЕ) ==================
@router.message(F.text.lower() == "упупа настройки")
async def settings_command_handler(message: types.Message):
    await send_settings_menu(message)

@router.callback_query(F.data.startswith("settings:"))
async def settings_callback_handler(query: types.CallbackQuery):
    await handle_settings_callback(query)
# ================== КОНЕЦ БЛОКА ИНТЕРАКТИВНЫХ НАСТРОЕК ==================

@router.message(lambda message: message.text and message.text.lower().startswith("упупа выйди из "))
async def leave_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    chat_identifier = message.text[14:].strip()
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
    await process_send_sms(message, chat_list, bot, sms_disabled_chats)

@router.message(lambda message: (message.text and message.text.lower().startswith("ммс ")) or 
                                (message.caption and message.caption.lower().startswith("ммс ")))
async def handle_send_mms(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id in sms_disabled_chats:
        await message.reply("СМС и ММС отключены в этом чате.")
        return
    await process_send_mms(message, chat_list, bot, sms_disabled_chats)

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

@router.message(F.text.lower() == "моя статистика")
async def show_personal_stats(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
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

@router.message(lambda message: message.text and message.text.lower() == "что за чат")
async def handle_chat_profile(message: types.Message):
    await process_chat_profile(message)

@router.message(lambda message: message.text and message.text.lower() == "кто я")
async def handle_user_profile(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    user_id = message.from_user.id
    chat_id = message.chat.id
    await process_user_profile(user_id, chat_id, message)

@router.message(lambda message: message.text and message.text.lower().startswith("пародия"))
async def handle_parody(message: types.Message):
   random_action = random.choice(actions)
   await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
   chat_id = message.chat.id
   await process_parody(message, chat_id)

@router.message(F.text.lower() == "викторина участники")
async def start_participant_quiz(message: Message, bot: Bot):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("ищем цитаты великих людей...")
    
    success, error_message = await process_participant_quiz_start(message, bot)
    
    await processing_msg.delete()
    if not success:
        await message.reply(error_message)

@router.message(F.text.lower().contains("викторина"))
async def start_quiz(message: Message, bot: Bot):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Генерирую вапросики...")
    success, error_message = await process_quiz_start(message, bot)
    await processing_msg.delete()
    if not success:
        await message.reply(error_message)

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

@router.message(lambda message: message.text and message.text.lower() in CHANNEL_SETTINGS.keys())
async def send_random_media(message: types.Message):
    await process_channel_command(message, CHANNEL_SETTINGS)

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

@router.message(F.text.lower() == "кем стать") 
async def choose_profession_command(message: types.Message):
    await get_random_okved_and_commentary(message)
    
@router.message(lambda message: message.text and message.text.lower().startswith("найди") and message.from_user.id not in BLOCKED_USERS)
async def handle_image_search(message: Message):
    random_action = random.choice(actions)
    query = message.text[len("найди"):].strip()
    success, response_message, image_data = await process_image_search(query)
    if success and image_data:
        await save_and_send_searched_image(message, image_data)
    elif response_message:
        await message.reply(response_message)

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

@router.message(is_distortion_command)
async def handle_distortion_command(message: types.Message):
    await handle_distortion_request(message)

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
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    
    processing_text = get_processing_message(message)
    processing_msg = await message.reply(processing_text)
    
    success, response = await process_whatisthere_unified(message)
    await processing_msg.delete()
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

@router.message(
    lambda message: (
        (message.photo and message.caption and "добавь" in message.caption.lower()) or 
        (message.text and "добавь" in message.text.lower() and message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document))
    ) and message.from_user.id not in BLOCKED_USERS
)
async def add_text_to_image(message: types.Message):
    await handle_add_text_command(message)

@router.message(
    lambda message: (
        message.from_user.id not in BLOCKED_USERS and
        message.text and (
            message.text.lower().startswith("нарисуй") or
            (message.text.lower().strip() == "нарисуй" and message.reply_to_message)
        )
    )
)
async def draw_image_gemini(message: types.Message):
    # "Нарисуй" вызывает Gemini
    await handle_draw_command(message)
    
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
    # "Сгенерируй" вызывает Kandinsky
    await handle_generate_command(message)

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
async def redraw_image_gemini(message: types.Message):
    # "Перерисуй" вызывает Gemini
    await handle_redraw_command(message)

@router.message(
    lambda message: message.text and 
    message.text.lower().strip() == "скаламбурь" and 
    message.from_user.id not in BLOCKED_USERS
)
async def pun_image_gemini(message: types.Message):
    # "Скаламбурь" вызывает Gemini
    await handle_pun_image_command(message)

@router.message(lambda message: message.text and message.text.lower() == "упупа погода" and message.from_user.id not in BLOCKED_USERS)
async def handle_weather_command(message: types.Message):
    await handle_current_weather_command(message)
        
@router.message(lambda message: message.text and message.text.lower().startswith("погода неделя") and message.from_user.id not in BLOCKED_USERS)
async def handle_weekly_forecast(message: types.Message):
    await handle_weekly_forecast_command(message)
    
@router.message(lambda message: message.text and 
                (message.text.lower().startswith("упупа запомни: мой др") or 
                 message.text.lower().startswith("упупа запомни мой др")) and 
                message.from_user.id not in BLOCKED_USERS)
async def handle_birthday_save_command(message: types.Message):
    await handle_birthday_command(message)

@router.message(lambda message: message.text and message.text.lower() == "упупа дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def birthday_list_command(message: types.Message):
    await handle_birthday_list_command(message)

@router.message(lambda message: message.text and message.text.lower().startswith("упупа поздравь ") and message.from_user.id not in BLOCKED_USERS)
async def test_greeting_command(message: types.Message):
    await handle_test_greeting_command(message)

@router.message(lambda message: message.text and message.text.lower() == "упупа все дни рождения" and message.from_user.id not in BLOCKED_USERS)
async def admin_birthday_list_command(message: types.Message):
    await handle_admin_birthday_list_command(message)

@router.message(F.text.lower() == "чобыло")
async def handle_chobylo(message: types.Message):
    random_action = random.choice(actions)
    await summarize_chat_history(message, model, LOG_FILE, actions)

@router.message(F.text.lower() == "упупа не болтай")
async def disable_dialog(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
    chat_settings[chat_id]["dialog_enabled"] = False
    save_chat_settings()
    await message.reply("Лана отъебитесь.")

@router.message(F.text.lower() == "упупа говори")
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

@router.message()
async def process_message(message: types.Message):
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
    
# ================== БЛОК 5: ЗАПУСК БОТА ==================
async def main():
    bot_statistics.init_db()
    chat_ids = ['-1001707530786', '-1001781970364']
    for chat_id in chat_ids:
        chat_id_int = int(chat_id)
        asyncio.create_task(schedule_daily_quiz(bot, chat_id_int))
    
    asyncio.create_task(birthday_scheduler(bot))
    
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
