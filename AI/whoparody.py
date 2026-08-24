#whoparody.py

import random
import logging
import asyncio
from aiogram import types
from features.lexicon_settings import (
    extract_user_messages,
    extract_messages_by_username,
    extract_messages_by_full_name,
    extract_chat_messages
)
from config import (
    model, LOG_FILE, gigachat_model, groq_ai, chat_settings,
    openrouter_ai, siliconflow_ai
)
from AI.dialog.settings import build_prompt_with_current_chat_prompt
from prompts import actions, PARODY_PROMPT

# --- ПРОМПТЫ ---

WHO_AM_I_PROMPT = """
Проанализируй следующие сообщения от пользователя и составь его портрет.
Опиши его манеру общения, возможные увлечения и характер, приводи уместные примеры его сообщений.
Стиль ответа полностью возьми из текущего промпта чата: сохрани его характер, тон, лексику,
степень серьёзности или сарказма и отношение к ненормативной лексике. Не подменяй текущую роль
отдельным образом "саркастичного аналитика".
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.
ВАЖНО: Постарайся уложиться в 3000 символов.

Вот сообщения для анализа:
{messages}
"""

CHAT_PROFILE_PROMPT = """
Проанализируй следующие сообщения из чата и составь портрет этого чата.
Опиши атмосферу, манеру общения участников и основные темы обсуждений, приводи уместные примеры.
Проведи отдельный краткий анализ по самым активным пользователям.
Стиль ответа полностью возьми из текущего промпта чата: сохрани его характер, тон, лексику,
степень серьёзности или сарказма и отношение к ненормативной лексике. Не подменяй текущую роль
отдельным образом "саркастичного аналитика".
Не пиши вступлений типа "на основе сообщений", просто выдавай готовую характеристику.
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
    """Генерирует непустой ответ активной моделью; при сбое использует Groq как fallback."""
    chat_key = str(chat_id)
    current_settings = chat_settings.get(chat_key, {})
    active_model = current_settings.get("active_model", "gemini")

    # Режим истории не подходит для анализа
    if active_model == "history":
        active_model = "gemini"

    logging.info(f"Генерация профиля с моделью {active_model} для чата {chat_id}")

    safety_settings = {
        "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
        "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
        "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
    }

    def sync_generate(model_name: str) -> str:
        if model_name == "gigachat":
            response = gigachat_model.generate_content(prompt, chat_id=chat_id)
            return response.text or ""
        if model_name == "groq":
            return groq_ai.generate_text(prompt) or ""
        if model_name == "openrouter":
            return openrouter_ai.generate_text(prompt) or ""
        if model_name == "siliconflow":
            return siliconflow_ai.generate_text(prompt) or ""

        # Gemini: как у "чобыло", отключаем safety-блокировку. require_text
        # превращает пустой успешный ответ в явную ошибку вместо молчания.
        response = model.generate_content(
            prompt,
            chat_id=chat_id,
            safety_settings=safety_settings,
            require_text=True,
        )
        return response.text or ""

    try:
        result = await asyncio.to_thread(sync_generate, active_model)
        if result and result.strip():
            return result.strip()
        raise RuntimeError(f"{active_model} вернул пустой ответ")
    except Exception as primary_error:
        logging.warning(
            "Основная модель не смогла сгенерировать профиль (%s): %s",
            active_model,
            primary_error,
        )

        # Поведение по образцу "чобыло": если основная модель молчит/блокируется,
        # пробуем запасную текстовую модель вместо сообщения "Модель промолчала".
        if active_model != "groq":
            try:
                fallback = await asyncio.to_thread(sync_generate, "groq")
                if fallback and fallback.strip():
                    logging.info("Профиль успешно сгенерирован через аварийный Groq fallback")
                    return fallback.strip()
            except Exception as fallback_error:
                logging.error(
                    "Groq fallback для профиля тоже завершился ошибкой: %s",
                    fallback_error,
                    exc_info=True,
                )

        raise primary_error

# --- ОСНОВНАЯ ЛОГИКА ---

async def process_user_profile(user_id, chat_id, message: types.Message):
    """Генерирует характеристику пользователя на основе его сообщений в стиле текущего промпта."""
    processing_msg = await message.reply("щас посмотрим, что ты за фрукт")

    messages = await extract_user_messages(user_id, chat_id)
    if not messages:
        await processing_msg.delete()
        await message.reply("Я тебя не знаю, иди нахуй.")
        return

    sample_size = min(400, len(messages))
    message_sample = random.sample(messages, sample_size)

    messages_text = "\n".join(message_sample)
    prompt = build_prompt_with_current_chat_prompt(
        str(chat_id),
        WHO_AM_I_PROMPT.format(messages=messages_text),
        task_name="анализ участника",
    )

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
    """Генерирует характеристику чата на основе сообщений в стиле текущего промпта."""
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

    prompt = build_prompt_with_current_chat_prompt(
        str(chat_id),
        CHAT_PROFILE_PROMPT.format(messages=messages_text),
        task_name="анализ чата",
    )

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
