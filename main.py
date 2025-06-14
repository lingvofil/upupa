# ================== БЛОК 0: Библиотеки ==================
import os
import random
import logging
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile, Message, PollAnswer, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.filters.command import Command
from gigachat import GigaChat
import json
import nest_asyncio
from datetime import datetime, timedelta
import re

nest_asyncio.apply()
# ================== БЛОК 1: Конфигурация ==================
from Config import *

# ================== БЛОК 2: СПРАвКА, ПРОМПТЫ, РАНГИ, СТОП-СЛОВА, КАНАЛЫ, ЖИВОТНЫЕ ==================
from Prompts import *

# ================== БЛОК 3.1: ОБЩИЕ НАСТРОЙКИ ==================
from Common_settings import *
        
# ================== БЛОК 3.2: НАСТРОЙКА ЧАТОВ ==================
from Chat_settings import *

# ================== БЛОК 3.3: НАСТРОЙКА СТАТИСТИКИ, РАНГОВ ==================
from Stat_rank_settings import *

# ================== БЛОК 3.4: НАСТРОЙКА ЛЕКСИКОНА ==================
from Lexicon_settings import *

# ================== БЛОК 3.5: НАСТРОЙКА СМС, ММС ==================
from SMS_settings import *

# ================== БЛОК 3.6: НАСТРОЙКА КТО Я, ЧТО ЗА ЧАТ, ПАРОДИЯ ==================
from whoparody import (
    process_user_profile,
    process_chat_profile,
    process_parody
)

# ================== БЛОК 3.7: НАСТРОЙКА ВИКТОРИНА ==================
from quiz import process_quiz_start, process_poll_answer, schedule_daily_quiz

# ================== БЛОК 3.8: НАСТРОЙКА ДОБАВЬ ОПИШИ ==================
from adddescribe import (
    process_image_description,
    get_photo_from_message,
    download_telegram_image,
    process_image,
    overlay_text_on_image,
    handle_add_text_command
)

# ================== БЛОК 3.9: НАСТРОЙКА ЧОТАМ ==================
from whatisthere import (
    process_audio_description, 
    process_video_description,
    process_image_whatisthere,
    process_gif_whatisthere
)

# ================== БЛОК 3.7: НАСТРОЙКА ПЕРЕСЫЛКИ МЕДИА ==================
from Channels_settings import process_channel_command

# ================== БЛОК 3.8: НАСТРОЙКА ПОИСКА ==================
from search import (
    handle_message,
    process_image_search,
    save_and_send_searched_image,
    process_gif_search,
    save_and_send_gif
)

# ================== БЛОК 3.9: НАСТРОЙКА ГЕНЕРАЦИИ КАРТИНОК ==================
from Picgeneration import handle_image_generation_command, handle_pun_image_command, handle_redraw_command

# ================== БЛОК 3.10: НАСТРОЙКА ПОГОДЫ ==================
from weather import (
    get_weather_with_fallback, 
    get_mock_weather, 
    format_weekly_forecast, 
    handle_current_weather_command, 
    handle_weekly_forecast_command
)
# ================== БЛОК 3.11: НАСТРОЙКА ГОВОРИЛКИ ==================
from talking import (
    update_chat_settings,
    update_conversation_history,
    format_chat_history,
    generate_response,
    handle_bot_conversation,
    get_current_chat_prompt,
    handle_list_prompts_command,
    handle_current_prompt_command,
    handle_set_prompt_command,
    handle_change_prompt_randomly_command,
    handle_poem_command,
    process_general_message
)
from random_reactions import process_random_reactions

# ================== БЛОК 3.15: НАСТРОЙКА ИМЕНИ ==================
from nameinfo import process_name_info

# ================== БЛОК 3.16: НАСТРОЙКА ЧОБЫЛО ==================
from summarize import summarize_chat_history
        
# ================== БЛОК 4: ХЭНДЛЕРЫ ==================
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

@router.message(lambda message: message.text and message.text.lower().startswith("упупа выйди из "))
async def leave_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Еще чо сделать?")
        return
    chat_identifier = message.text[14:].strip()  # Убираем "упупа выйди из "
    await process_leave_chat(message, chat_identifier)

@router.message(lambda message: message.text and message.text.lower() == "обновить чаты")
async def update_all_chats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Иди нахуй, у тебя нет прав на это.")
        return
    await process_update_all_chats(message, bot)

@router.message(lambda message: message.text and message.text.lower() == "где сидишь")
async def handle_where_sits(message: types.Message):
    global chat_list
    response = get_chats_list(message.chat.id, message.chat.title, message.chat.username)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower() == "отключи смс")
async def disable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id  # ID отправителя
    response = await process_disable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and message.text.lower() == "включи смс")
async def enable_sms(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    response = await process_enable_sms(chat_id, user_id, bot)
    await message.reply(response)

@router.message(lambda message: message.text and (message.text.lower().startswith("смс ") or message.text.lower().startswith("ммс ")))
async def check_sms_mms_permission(message: types.Message):
    chat_id = str(message.chat.id)
    await process_check_sms_mms_permission(chat_id, message)

@router.message(lambda message: message.text and message.text.lower().startswith("смс "))
async def handle_send_sms(message: types.Message):
    from SMS_settings import process_send_sms
    global chat_list
    await process_send_sms(message, chat_list, bot, sms_disabled_chats)

@router.message(lambda message: (message.text and message.text.lower().startswith("ммс ")) or 
                                (message.caption and message.caption.lower().startswith("ммс ")))
async def handle_send_mms(message: types.Message):
    from SMS_settings import process_send_mms
    await process_send_mms(message, chat_list, bot, sms_disabled_chats)

@router.message(lambda message: message.text and message.text.lower() == "мой лексикон")
async def handle_my_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    user_id = message.from_user.id
    chat_id = message.chat.id  # Ограничиваем статистику этим чатом
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
    chat_id = message.chat.id  # Ограничиваем анализ этим чатом
    await process_user_profile(user_id, chat_id, message)

@router.message(lambda message: message.text and message.text.lower().startswith("пародия"))
async def handle_parody(message: types.Message):
   random_action = random.choice(actions)
   await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
   chat_id = message.chat.id  # Ограничиваем выбор фраз этим чатом
   await process_parody(message, chat_id)

@router.message(F.text.lower().contains("викторина"))
async def start_quiz(message: Message, bot: Bot):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Генерирую вапросики...")
    success, error_message = await process_quiz_start(message, bot)
    await processing_msg.delete()
    if not success:
        await message.reply(error_message)

@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    await process_poll_answer(poll_answer, bot)

@router.message(lambda message: message.text and message.text.lower() in CHANNEL_SETTINGS.keys())
async def send_random_media(message: types.Message):
    await process_channel_command(message, CHANNEL_SETTINGS)

@router.message(lambda message: 
    message.text and 
    message.text.lower().startswith("имя ") and 
    message.from_user and  # Убедимся, что у сообщения есть отправитель
    message.from_user.id not in BLOCKED_USERS
)
async def handle_name_info(message: types.Message):
    random_action = random.choice(actions)
    success, response = await process_name_info(message)
    await message.reply(response)

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

@router.message(lambda message: 
    (
        (message.audio or message.voice) and message.caption and "чотам" in message.caption.lower()
    ) 
    or 
    (
        message.text and "чотам" in message.text.lower() and message.reply_to_message and 
        (message.reply_to_message.audio or message.reply_to_message.voice)
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_audio_description(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Слушою...")
    success, response = await process_audio_description(message)
    await processing_msg.delete()
    await message.reply(response)

@router.message(lambda message: 
    (
        (message.video and message.caption and "чотам" in message.caption.lower())
    )
    or 
    (
        message.text and "чотам" in message.text.lower() and message.reply_to_message and 
        message.reply_to_message.video
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_video_description(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Сматрю...")
    success, response = await process_video_description(message)
    await processing_msg.delete()
    await message.reply(response)

# НОВЫЙ хэндлер для "чотам" с картинкой
@router.message(lambda message: 
    (
        (message.photo and message.caption and "чотам" in message.caption.lower())
    )
    or 
    (
        message.text and "чотам" in message.text.lower() and message.reply_to_message and 
        message.reply_to_message.photo
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_image_whatisthere(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Рассматриваю ето художество...")
    success, response = await process_image_whatisthere(message)
    await processing_msg.delete()
    await message.reply(response)

# НОВЫЙ хэндлер для "чотам" с гифкой
@router.message(lambda message: 
    (
        (message.animation and message.caption and "чотам" in message.caption.lower())
    )
    or 
    (
        message.text and "чотам" in message.text.lower() and message.reply_to_message and 
        message.reply_to_message.animation
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_gif_whatisthere(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Да не дергайся ты...")
    success, response = await process_gif_whatisthere(message)
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
async def generate_image(message: types.Message):
    await handle_image_generation_command(message)

@router.message(
    lambda message: (
        (
            # Вариант 1: Фото с подписью "перерисуй"
            (message.photo and message.caption and "перерисуй" in message.caption.lower()) or
            # Вариант 2: Документ с подписью "перерисуй"  
            (message.document and message.caption and "перерисуй" in message.caption.lower()) or
            # Вариант 3: Реплай на фото/документ с текстом "перерисуй"
            (message.text and "перерисуй" in message.text.lower() and message.reply_to_message and 
             (message.reply_to_message.photo or message.reply_to_message.document))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def redraw_image(message: types.Message):
    from Picgeneration import handle_redraw_command
    await handle_redraw_command(message)

@router.message(
    lambda message: message.text and 
    message.text.lower().strip() == "скаламбурь" and 
    message.from_user.id not in BLOCKED_USERS
)
async def generate_pun_with_image(message: types.Message):
    await handle_pun_image_command(message)

@router.message(lambda message: message.text and message.text.lower() == "упупа погода" and message.from_user.id not in BLOCKED_USERS)
async def handle_weather_command(message: types.Message):
    await handle_current_weather_command(message)
        
@router.message(lambda message: message.text and message.text.lower().startswith("погода неделя") and message.from_user.id not in BLOCKED_USERS)
async def handle_weekly_forecast(message: types.Message):
    await handle_weekly_forecast_command(message)

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
    
# ================== БЛОК 5: ЗАПУСК БОТА ==================
async def main():
    # Сначала создаём задачи для викторин
    chat_ids = ['-1001707530786', '-1001781970364']  # Список ID чатов для ежедневной викторины
    for chat_id in chat_ids:
        chat_id_int = int(chat_id)
        asyncio.create_task(schedule_daily_quiz(bot, chat_id_int))

    # Настраиваем и запускаем бота
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)

# Запуск бота
if __name__ == "__main__":
    asyncio.run(main())