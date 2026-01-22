#whoparody.py

import random
import logging
import asyncio
from aiogram import types
from lexicon_settings import (
    extract_user_messages,
    extract_messages_by_username,
    extract_messages_by_full_name,
    extract_chat_messages
)
from config import model, LOG_FILE, gigachat_model, groq_ai, chat_settings
from prompts import actions, PARODY_PROMPT

# --- ПРОМПТЫ ---

WHO_AM_I_PROMPT = """
Проанализируй следующие сообщения от пользователя. На основе этих сообщений составь его портрет. 
Будь максимально саркастичным, язвительным и не стесняйся в выражениях. Используй ненормативную лексику. 
Опиши его манеру общения, возможные увлечения и характер, приводи примеры его сообщений. 
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.
ВАЖНО: Постарайся уложиться в 3000 символов.

Вот сообщения для анализа:
{messages}
"""

CHAT_PROFILE_PROMPT = """
Проанализируй следующие сообщения из чата. На основе этих сообщений составь портрет этого чата. 
Будь максимально саркастичным, язвительным и не стесняйся в выражениях. Используй ненормативную лексику. 
Опиши атмосферу, манеру общения участников и возможные темы обсуждений, приводи примеры.
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.
Проведи отдельный краткий анализ по самым активным пользователям.
ВАЖНО: Постарайся уложиться в 3500 символов.

Вот сообщения для анализа:
{messages}
"""

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def send_long_message(message: types.Message, text: str):
    """Разбивает текст на части по 4000 символов и отправляет их."""
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        await message.reply(text)
    else:
        # Разбиваем текст на куски
        chunks = [text[i:i + MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk)
            else:
                # Последующие части отправляем обычным сообщением, чтобы не спамить реплаями
                await message.answer(chunk)
            # Небольшая пауза, чтобы не поймать Flood Limit от Telegram
            await asyncio.sleep(0.5)

async def generate_with_active_model(prompt: str, chat_id: int) -> str:
    """Генерирует ответ с использованием активной модели для чата"""
    try:
        chat_key = str(chat_id)
        current_settings = chat_settings.get(chat_key, {})
        active_model = current_settings.get("active_model", "gemini")
        
        # Режим истории не подходит для анализа
        if active_model == "history":
            active_model = "gemini"
        
        logging.info(f"Генерация с моделью {active_model} для чата {chat_id}")
        
        def sync_generate():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=chat_id)
                return response.text
            elif active_model == "groq":
                return groq_ai.generate_text(prompt)
            else:  # gemini
                response = model.generate_content(prompt, chat_id=chat_id)
                return response.text
        
        return await asyncio.to_thread(sync_generate)
        
    except Exception as e:
        logging.error(f"Ошибка при генерации с активной моделью: {e}")
        raise

# --- ОСНОВНАЯ ЛОГИКА ---

async def process_user_profile(user_id, chat_id, message: types.Message):
    """Генерирует саркастичную характеристику пользователя на основе его сообщений."""
    processing_msg = await message.reply("щас посмотрим, что ты за фрукт")
    
    messages = await extract_user_messages(user_id, chat_id)
    if not messages:
        await processing_msg.delete()
        await message.reply("Я тебя не знаю, иди нахуй.")
        return
        
    sample_size = min(400, len(messages))
    message_sample = random.sample(messages, sample_size)
    
    messages_text = "\n".join(message_sample)
    prompt = WHO_AM_I_PROMPT.format(messages=messages_text)
    
    try:
        random_action = random.choice(actions)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
        
        description = await generate_with_active_model(prompt, chat_id)
    except Exception as e:
        logging.error(f"Ошибка при анализе личности 'кто я': {e}")
        description = f"Не могу составить твой портрет, ты слишком сложная и непонятная хуйня. Ошибка: {e}"
    
    await processing_msg.delete()
    await send_long_message(message, description)

async def process_chat_profile(message: types.Message):
    """Генерирует саркастичную характеристику чата на основе сообщений в нем."""
    chat_id = message.chat.id
    processing_msg = await message.reply("Анализирую этот гадюшник...")

    messages = await extract_chat_messages(chat_id)
    logging.info(f"Извлечено {len(messages)} сообщений для чата: {chat_id}")
    
    if not messages:
        await processing_msg.delete()
        await message.reply("В этом чате такая тишина, что даже мухи дохнут со скуки. Нечего анализировать.")
        return

    sample_size = min(400, len(messages))
    message_sample = random.sample(messages, sample_size)
    messages_text = "\n".join(message_sample)

    prompt = CHAT_PROFILE_PROMPT.format(messages=messages_text)

    try:
        random_action = random.choice(actions)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
        description = await generate_with_active_model(prompt, chat_id)
    except Exception as e:
        logging.error(f"Ошибка при генерации характеристики чата: {e}")
        description = "Не могу понять, что это за притон. Слишком много кринжа."
        
    await processing_msg.delete()
    await send_long_message(message, description)

async def process_parody(message: types.Message, chat_id: int):
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
       parody_text = await generate_with_active_model(prompt, chat_id)
   except Exception as e:
       logging.error(f"Ошибка генерации пародии: {e}")
       parody_text = "Ошибка при создании пародии."
       
   response_text = f"{'@' + username if username else full_name}:\n\n{parody_text}"
   
   await send_long_message(message, response_text)
