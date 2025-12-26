#summarize.py

import logging
import asyncio
import re
import time
from datetime import datetime, timedelta
from aiogram import types
import random

from config import LOG_FILE, model 
from prompts import actions 

def _get_chat_messages(log_file_path: str, chat_id: str, start_time: datetime):
    """
    Вспомогательная функция для чтения и парсинга логов.
    Возвращает список сообщений, словарь пользователей и имя чата.
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
                            
                            # Фильтруем по ID чата и времени
                            if str(log_chat_id) == chat_id and log_timestamp >= start_time:
                                display_name = display_name.strip() if display_name and display_name.strip() else username
                                # Добавляем дату для годового отчета, чтобы бот понимал хронологию
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

async def summarize_chat_history(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """
    Обычная сводка за последние 12 часов (команда 'чобыло').
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

    await _generate_and_send_summary(message, chat_model, summary_prompt, action_list, "Пишу доклад...")

async def summarize_year(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """
    Итоги года (команда 'итоги года').
    С защитой от переполнения контекста (Limit 429).
    """
    chat_id = str(message.chat.id)
    now = datetime.now()
    time_threshold = now - timedelta(days=365) 

    status_msg = await message.reply("Ого, итоги года? Поднимаю архивы, это займет время...")

    messages_to_summarize, users_in_period, chat_name = await asyncio.to_thread(
        _get_chat_messages, log_file_path, chat_id, time_threshold
    )

    if not messages_to_summarize:
        await status_msg.edit_text("За последний год логов не найдено. Видимо, я спал.")
        return

    # === ЛОГИКА СЖАТИЯ ДЛЯ ИЗБЕЖАНИЯ ОШИБКИ 429 ===
    # Оцениваем объем текста. Грубо: 1 токен ~ 4 символа.
    # Лимит Free Tier для Flash часто около 1 млн токенов/минуту, но Flash-Lite или загрузка сети могут быть меньше.
    # Безопасный порог для одного запроса "без нервов" ~ 60 000 - 80 000 символов.
    
    total_chars_approx = sum(len(m['text']) for m in messages_to_summarize)
    MAX_SAFE_CHARS = 80000 

    if total_chars_approx > MAX_SAFE_CHARS:
        # Вычисляем шаг, чтобы проредить сообщения равномерно
        step = (total_chars_approx // MAX_SAFE_CHARS) + 1
        messages_to_summarize = messages_to_summarize[::step]
        logging.info(f"Log too large ({total_chars_approx} chars). Downsampling with step {step}. New count: {len(messages_to_summarize)}")
        await status_msg.edit_text(f"Логов слишком дохуя ({total_chars_approx} симв.), читаю по диагонали (каждое {step}-е сообщение)...")
    
    summary_input_text = f"Хронология сообщений чата {chat_name} за ГОД (выборка, всего {len(messages_to_summarize)} сообщений):\n\n"
    for msg in messages_to_summarize:
        summary_input_text += f"[{msg['date']}] {msg['display_name']}: {msg['text']}\n"

    user_mentions_list = [u["display_name"] for u in users_in_period.values() if u["display_name"]]
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "всех бродяг"

    summary_prompt = f"""Ты подводишь ИТОГИ ГОДА для чата {chat_name}.
    Входящие данные — это лог переписки за 12 месяцев.
    
    Твоя задача написать эпичный, смешной и немного оскорбительный отчет.
    
    Структура отчета:
    1. 🏆 **Главные события года**: Выдели 3-5 основных сюжетных арок или срачей.
    2. 🤡 **Номинации года**: Придумай смешные номинации (например: "Душнила года", "Генератор кринжа", "Пропажа года"). Раздай их участникам: {user_mentions_str}.
    3. 💬 **Золотой фонд цитат**: Выбери 3 самые странные или смешные цитаты из лога.
    4. 📉 **Общая атмосфера**: Как прошел год? Деградировали или эволюционировали?
    
    Стиль: Сарказм, обсценная лексика (мат умеренно, но метко), панибратство.
    Объем: Будь подробен, но не лей воду.
    
    Лог чата:
    {summary_input_text}
    
    Твой отчет:
    """

    # Не удаляем status_msg сразу, удалим внутри генерации или после
    await _generate_and_send_summary(message, chat_model, summary_prompt, action_list, "Анализирую этот пиздец...", status_msg)

async def _generate_and_send_summary(message: types.Message, chat_model, prompt: str, action_list: list, wait_text: str, prev_msg: types.Message = None):
    """
    Внутренняя функция для отправки запроса в LLM с ретраями при 429.
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

        def sync_gemini_call_with_retry():
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    # Передаем chat_id, если обертка это поддерживает
                    response = chat_model.generate_content(prompt, chat_id=message.chat.id)
                    return response.text
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str:
                        if attempt < max_retries:
                            # Пытаемся выпарсить время ожидания, если его нет - ждем минуту
                            wait_time = 60
                            logging.warning(f"Quota exceeded (429). Waiting {wait_time}s before retry {attempt+1}/{max_retries}...")
                            time.sleep(wait_time) 
                            continue
                        else:
                            raise e # Ретраи кончились
                    else:
                        raise e

        # Запускаем в треде, чтобы time.sleep не вешал бота
        summary_response = await asyncio.to_thread(sync_gemini_call_with_retry)
        
        await processing_msg.delete()

        # === FIX: Защита от ошибок Markdown ===
        try:
            await message.reply(summary_response, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"Markdown parsing failed ({e}), sending plain text fallback.")
            await message.reply(summary_response)

    except Exception as e:
        logging.error(f"API Error during summarization: {e}")
        error_text = str(e)
        if "429" in error_text:
            await message.reply("🤖 Google жалуется на лимиты (429). Слишком много текста. Попробуй позже или скажи админу уменьшить выборку.")
        else:
            await message.reply(f"🤖 Не удалось сформировать отчет. Ошибка: {error_text[:100]}...")
