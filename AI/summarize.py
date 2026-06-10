#summarize.py

import logging
import asyncio
import re
import time
from datetime import datetime, timedelta
from aiogram import types
import random
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from config import LOG_FILE, model, gigachat_model, groq_ai, chat_settings
from prompts import actions
from features.chat_settings import save_chat_settings

def _get_chat_messages(log_file_path: str, chat_id: str, start_time: datetime):
    """
    Вспомогательная функция для чтения и парсинга логов.
    """
    messages = []
    users_found = {}
    chat_name = None
    
    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
            for line in lines:
                try:
                    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) - User (\d+) \((.*?)\) \[(.*?)\]: (.*?)$", line)
                    
                    if match:
                        timestamp_str, log_chat_id, current_chat_name, user_id, username, display_name, text = match.groups()
                        
                        if not text.strip():
                            continue
                        
                        # Сохраняем имя чата
                        if str(log_chat_id) == chat_id and not chat_name:
                            chat_name = current_chat_name
                        
                        try:
                            log_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f")
                            
                            if str(log_chat_id) == chat_id and log_timestamp >= start_time:
                                display_name = display_name.strip() if display_name and display_name.strip() else username
                                date_str = log_timestamp.strftime("%d.%m") 
                                messages.append({
                                    "date": date_str,
                                    "username": username, 
                                    "display_name": display_name, 
                                    "text": text.strip()
                                })
                                
                                if username and username.lower() not in ['none', 'null']:
                                    users_found[user_id] = {"username": username, "display_name": display_name}
                        
                        except ValueError as e:
                            continue
                    
                except Exception:
                    continue
                    
    except FileNotFoundError:
        logging.warning(f"Log file not found: {log_file_path}")
        return [], {}, None

    return messages, users_found, chat_name


def _get_active_model(chat_id: str):
    """Определяет активную модель для чата"""
    current_settings = chat_settings.get(chat_id, {})
    active_model = current_settings.get("active_model", "gemini")
    return active_model


def _compress_messages_for_groq(messages: list, max_chars: int = 15000) -> tuple[list, int]:
    """
    Сжимает сообщения для Groq API с учетом лимитов.
    Возвращает (сжатые_сообщения, коэффициент_сжатия)
    """
    total_chars = sum(len(m['text']) for m in messages)
    
    if total_chars <= max_chars:
        return messages, 1
    
    # Вычисляем коэффициент сжатия
    compression_ratio = max(2, total_chars // max_chars + 1)
    
    # Берём каждое N-е сообщение
    compressed = messages[::compression_ratio]
    
    logging.info(f"Groq compression: {len(messages)} msgs → {len(compressed)} msgs (ratio: {compression_ratio})")
    
    return compressed, compression_ratio


async def _generate_with_active_model(prompt: str, chat_id: str, safety_settings=None, is_summarization=False):
    """Генерирует текст с использованием активной модели чата"""
    active_model = _get_active_model(chat_id)
    
    # Режим истории не подходит для суммаризации
    if active_model == "history":
        active_model = "gemini"
        logging.info("Summarize: режим 'history' не поддерживается, используем Gemini")
    
    logging.info(f"Summarize: используется модель {active_model}")
    
    def sync_model_call_with_retry():
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                if active_model == "gigachat":
                    response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                    return response.text
                elif active_model == "groq":
                    # Используем специальную модель для суммаризации
                    if is_summarization:
                        logging.info(f"Используется модель суммаризации: {groq_ai.summarization_model}")
                        # Временно меняем модель
                        original_model = groq_ai.text_model
                        groq_ai.text_model = groq_ai.summarization_model
                        try:
                            result = groq_ai.generate_text(prompt, max_tokens=2048)
                        finally:
                            # Восстанавливаем исходную модель
                            groq_ai.text_model = original_model
                    else:
                        result = groq_ai.generate_text(prompt, max_tokens=2048)
                    return result or "Groq вернул пустой ответ"
                else:  # gemini
                    response = model.generate_content(
                        prompt, 
                        safety_settings=safety_settings,
                        chat_id=int(chat_id)
                    )
                    return response.text
                    
            except Exception as e:
                error_str = str(e)
                if "429" in error_str:
                    if attempt < max_retries:
                        wait_time = 30
                        logging.warning(f"Quota 429. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise e
                elif "413" in error_str or "request_too_large" in error_str:
                    # Специфичная ошибка Groq - промпт слишком большой
                    return "⚠️ Логов слишком много для Groq. Переключитесь на Gemini командой 'упупа модель gemini' или попробуйте меньший период."
                elif "PROHIBITED" in error_str or "block_reason" in error_str:
                    return "Google зассал и заблокировал ответ из-за 'недопустимого контента'. Слишком грязно ругаетесь."
                else:
                    raise e
    
    return await asyncio.to_thread(sync_model_call_with_retry)


async def summarize_chat_history(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """
    Обычная сводка за последние 12 часов.
    """
    chat_id = str(message.chat.id)
    now = datetime.now()
    time_threshold = now - timedelta(hours=12)

    await message.reply("Щас всех вас сдам...")

    messages_to_summarize, users_in_period, chat_name = await asyncio.to_thread(
        _get_chat_messages, log_file_path, chat_id, time_threshold
    )

    if not messages_to_summarize:
        await message.reply(f"За последние 12 часов в чате {chat_name or chat_id} нихуя не было.")
        return

    # Сжатие для Groq, если используется
    active_model = _get_active_model(chat_id)
    compression_ratio = 1
    
    if active_model == "groq":
        messages_to_summarize, compression_ratio = _compress_messages_for_groq(messages_to_summarize, max_chars=15000)
        if compression_ratio > 1:
            await message.reply(f"пишу доклад")

    summary_input_text = f"Сообщения из чата {chat_name} за последние 12 часов (всего {len(messages_to_summarize)} сообщений):\n\n"
    for msg in messages_to_summarize:
        summary_input_text += f"{msg['display_name']}: {msg['text']}\n"

    user_mentions_list = [u["display_name"] for u in users_in_period.values() if u["display_name"]]
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "участников"

    summary_prompt = f"""Просуммируй следующие сообщения из чата {chat_name}. Сделай краткое изложение в свободной форме (с сарказмом и обсценной лексикой), разбей на абзацы. Не более 200 слов. 
    Упомяни участников беседы по имени (без символа @): {user_mentions_str}.
    Если сообщений мало, можно сделать совсем короткую сводку в один абзац.

    Вот сообщения:
    {summary_input_text}

    Суммаризация:
    """

    await _generate_and_send_summary(message, chat_id, summary_prompt, action_list, "Пишу доклад...")


async def summarize_year(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """
    Итоги года.
    """
    chat_id = str(message.chat.id)
    now = datetime.now()
    time_threshold = now - timedelta(days=365) 

    status_msg = await message.reply("Я долго терпел вас, уебков")

    messages_to_summarize, users_in_period, chat_name = await asyncio.to_thread(
        _get_chat_messages, log_file_path, chat_id, time_threshold
    )

    if not messages_to_summarize:
        await status_msg.edit_text("За последний год логов не найдено. Видимо, я спал.")
        return

    # Определяем активную модель для выбора лимита сжатия
    active_model = _get_active_model(chat_id)
    
    # Для Groq - более агрессивное сжатие
    if active_model == "groq":
        max_safe_chars = 12000  # Groq имеет меньший контекст
    else:
        max_safe_chars = 30000  # Gemini/GigaChat
    
    total_chars_approx = sum(len(m['text']) for m in messages_to_summarize)
    compression_ratio = 1

    if total_chars_approx > max_safe_chars:
        step = (total_chars_approx // max_safe_chars) + 1
        messages_to_summarize = messages_to_summarize[::step]
        compression_ratio = step
        logging.info(f"Log compressed. Original chars: {total_chars_approx}. New count: {len(messages_to_summarize)} msgs.")
        await status_msg.edit_text(f"Логов дохера ({total_chars_approx} симв.), читаю каждое {step}-е сообщение...")
    
    summary_input_text = f"Хронология сообщений чата {chat_name} за ГОД (выборка):\n\n"
    for msg in messages_to_summarize:
        summary_input_text += f"[{msg['date']}] {msg['display_name']}: {msg['text']}\n"

    user_mentions_list = [u["display_name"] for u in users_in_period.values() if u["display_name"]]
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "всех бродяг"

    summary_prompt = f"""Ты подводишь ИТОГИ ГОДА для чата {chat_name}.
    Входящие данные — это лог переписки за 12 месяцев.
    
    Твоя задача написать эпичный, смешной и немного оскорбительный отчет.
    
    Структура:
    1. 🏆 **Главные события года**: 3-5 основных тем.
    2. 🤡 **Номинации года**: Придумай смешные номинации ("Душнила", "Спамер" и т.д.) для: {user_mentions_str}.
    3. 💬 **Золотой фонд цитат**: 3 смешные цитаты из лога.
    4. 📉 **Итог**: Деградировали или эволюционировали?
    
    Стиль: Сарказм, мат (умеренно). Ты циничный бот.
    Используй Markdown.
    
    Лог:
    {summary_input_text}
    """

    await _generate_and_send_summary(message, chat_id, summary_prompt, action_list, "Анализирую этот пиздец...", status_msg)


async def _generate_and_send_summary(message: types.Message, chat_id: str, prompt: str, action_list: list, wait_text: str, prev_msg: types.Message = None):
    """
    Отправка в LLM с ретраями и разбивкой длинных сообщений.
    """
    try:
        random_action = random.choice(action_list)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
        
        if prev_msg:
            try:
                await prev_msg.edit_text(wait_text)
                processing_msg = prev_msg
            except:
                processing_msg = await message.reply(wait_text)
        else:
            processing_msg = await message.reply(wait_text)

        # Отключение фильтров безопасности (только для Gemini)
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        summary_response = await _generate_with_active_model(prompt, chat_id, safety_settings, is_summarization=True)
        
        await processing_msg.delete()

        # === ЛОГИКА РАЗБИВКИ НА ЧАСТИ (MAX 4096) ===
        if len(summary_response) <= 4096:
            try:
                await message.reply(summary_response, parse_mode="Markdown")
            except Exception:
                await message.reply(summary_response)
        else:
            # Разбиваем текст на куски
            parts = []
            while summary_response:
                if len(summary_response) <= 4096:
                    parts.append(summary_response)
                    break
                
                # Ищем перенос строки ближе к концу лимита
                split_index = summary_response.rfind('\n', 0, 4096)
                if split_index == -1:
                    # Если нет переноса, ищем пробел
                    split_index = summary_response.rfind(' ', 0, 4096)
                
                if split_index == -1:
                    # Если вообще ничего нет, режем жестко
                    split_index = 4096
                
                parts.append(summary_response[:split_index])
                summary_response = summary_response[split_index:].lstrip() # Убираем пробелы в начале след. куска

            # Отправляем куски
            for i, part in enumerate(parts):
                try:
                    # Первое сообщение - reply, остальные просто в чат
                    if i == 0:
                        await message.reply(part, parse_mode="Markdown")
                    else:
                        await message.answer(part, parse_mode="Markdown")
                except Exception as e:
                    logging.warning(f"Markdown failed for part {i}, sending plain text.")
                    if i == 0:
                        await message.reply(part)
                    else:
                        await message.answer(part)

    except Exception as e:
        logging.error(f"Summarization Error: {e}")
        await message.reply(f"🤖 Ошибка: {str(e)[:100]}...")
