import random
import logging
from aiogram import types
from lexicon_settings import (
    extract_user_messages,
    extract_messages_by_username,
    extract_messages_by_full_name,
    extract_chat_messages  # Используем существующую функцию
)
from config import model, LOG_FILE
from prompts import actions, PARODY_PROMPT

# --- ПРОМПТЫ ---

WHO_AM_I_PROMPT = """
Проанализируй следующие сообщения от пользователя. На основе этих сообщений составь его портрет. 
Будь максимально саркастичным, язвительным и не стесняйся в выражениях. Используй ненормативную лексику. 
Опиши его манеру общения, возможные увлечения и характер, приводи примеры его сообщений. 
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.

Вот сообщения для анализа:
{messages}
"""

CHAT_PROFILE_PROMPT = """
Проанализируй следующие сообщения из чата. На основе этих сообщений составь портрет этого чата. 
Будь максимально саркастичным, язвительным и не стесняйся в выражениях. Используй ненормативную лексику. 
Опиши атмосферу, манеру общения участников и возможные темы обсуждений, приводи примеры.
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.
Проведи отдельный краткий анализ по самым активным пользователям.

Вот сообщения для анализа:
{messages}
"""

# --- ОСНОВНАЯ ЛОГИКА ---

# Обновленная логика обработки "кто я"
async def process_user_profile(user_id, chat_id, message: types.Message):
    """Генерирует саркастичную характеристику пользователя на основе его сообщений."""
    # ДОБАВЛЕНО: Промежуточное сообщение
    processing_msg = await message.reply("щас посмотрим, что ты за фрукт")
    
    # Собираем сообщения пользователя только в этом чате
    messages = await extract_user_messages(user_id, chat_id)
    if not messages:
        await processing_msg.delete()
        await message.reply("Я тебя не знаю, иди нахуй.")
        return
        
    # Выбираем 400 случайных сообщений или все, если их меньше
    sample_size = min(400, len(messages))
    message_sample = random.sample(messages, sample_size)
    
    # Формируем текст для промпта
    messages_text = "\n".join(message_sample)
    prompt = WHO_AM_I_PROMPT.format(messages=messages_text)
    
    try:
        random_action = random.choice(actions)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
        
        response = model.generate_content(prompt)
        description = response.text
    except Exception as e:
        logging.error(f"Ошибка при анализе личности 'кто я': {e}")
        description = f"Не могу составить твой портрет, ты слишком сложная и непонятная хуйня. Ошибка: {e}"
    
    await processing_msg.delete()
    await message.reply(description)

# ИСПРАВЛЕННАЯ логика обработки "что за чат"
async def process_chat_profile(message: types.Message):
    """Генерирует саркастичную характеристику чата на основе сообщений в нем."""
    chat_id = message.chat.id
    # ДОБАВЛЕНО: Промежуточное сообщение
    processing_msg = await message.reply("Анализирую этот гадюшник...")

    # Используем правильную функцию из Lexicon_settings.py
    messages = await extract_chat_messages(chat_id)
    logging.info(f"Извлечено {len(messages)} сообщений для чата: {chat_id}")
    
    if not messages:
        await processing_msg.delete()
        await message.reply("В этом чате такая тишина, что даже мухи дохнут со скуки. Нечего анализировать.")
        return

    # Выбираем 400 случайных сообщений или все, если их меньше
    sample_size = min(400, len(messages))
    message_sample = random.sample(messages, sample_size)
    messages_text = "\n".join(message_sample)

    prompt = CHAT_PROFILE_PROMPT.format(messages=messages_text)

    try:
        random_action = random.choice(actions)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
        response = model.generate_content(prompt)
        description = response.text
    except Exception as e:
        logging.error(f"Ошибка при генерации характеристики чата: {e}")
        description = "Не могу понять, что это за притон. Слишком много кринжа."
        
    await processing_msg.delete()
    await message.reply(description)

# Логика обработки "пародия" (без изменений)
async def process_parody(message, chat_id):
   random_action = random.choice(actions)
   await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
   parts = message.text.split(maxsplit=1)
   if len(parts) < 2:
       await message.reply("Неверный формат. Используй: пародия @username или пародия name")
       return
       
   query = parts[1].strip()
   username, full_name = None, None
   
   if query.startswith("@"):
       username = query[1:]
       messages = await extract_messages_by_username(username, chat_id)
   else:
       full_name = query
       messages = await extract_messages_by_full_name(full_name, chat_id)
       
   if not messages:
       await message.reply(f"Этот хуй еще не достоин")
       return
       
   parody_lines = random.sample(messages, min(20, len(messages)))
   prompt = PARODY_PROMPT.format(phrases="\n".join(parody_lines))
   
   try:
       response = model.generate_content(prompt)
       parody_text = response.text
   except Exception as e:
       logging.error(f"Ошибка генерации пародии: {e}")
       parody_text = "Ошибка при создании пародии."
       
   response_text = f"{'@' + username if username else full_name}:\n\n{parody_text}"
   
   await message.reply(response_text)
