import logging
import asyncio
import re
from datetime import datetime, timedelta
from aiogram import types
import random # Needed for random actions

from config import LOG_FILE, model # Make sure model is imported/available
from prompts import actions # Make sure actions list is available

async def summarize_chat_history(message: types.Message, chat_model, log_file_path: str, action_list: list):
    chat_id = str(message.chat.id)
    now = datetime.now()
    twenty_four_hours_ago = now - timedelta(hours=12)
    messages_to_summarize = []
    users_in_period = {}  # To store user_id: name and username mapping for mentions
    chat_name = None  # Добавляем переменную для хранения имени чата

    try:
        logging.info(f"Attempting to read log file: {log_file_path}")
        await message.reply("Щас всех вас сдам...")
        
        with open(log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            logging.info(f"Read {len(lines)} lines from log file")
            
            for line in lines:
                try:
                    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) - User (\d+) \((.*?)\) \[(.*?)\]: (.*?)$", line)
                    
                    if match:
                        timestamp_str, log_chat_id, current_chat_name, user_id, username, display_name, text = match.groups()
                        
                        # Если текст сообщения пуст, пропускаем его
                        if not text.strip():
                            continue
                        
                        # Сохраняем имя чата при первом совпадении
                        if str(log_chat_id) == chat_id and not chat_name:
                            chat_name = current_chat_name
                        
                        try:
                            log_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f")
                            
                            if str(log_chat_id) == chat_id and log_timestamp >= twenty_four_hours_ago:
                                display_name = display_name.strip() if display_name and display_name.strip() else username
                                messages_to_summarize.append({"username": username, "display_name": display_name, "text": text.strip()})
                                
                                if username and username.lower() not in ['none', 'null']:
                                    users_in_period[user_id] = {"username": username, "display_name": display_name}
                                
                        except ValueError as e:
                            logging.warning(f"Invalid timestamp format: {timestamp_str}, error: {e}")
                            continue
                    else:
                        logging.warning(f"Unable to parse log line: {line.strip()}")
                        
                except Exception as e:
                    logging.error(f"Error parsing log line '{line.strip()}': {e}")
                    continue

    except FileNotFoundError:
        await message.reply(f"Журнал сообщений не найден по пути {log_file_path}. Возможно, он еще не создан.")
        return
    except Exception as e:
        logging.error(f"Error reading log file for summary: {e}")
        await message.reply(f"Произошла ошибка при чтении истории сообщений: {str(e)}")
        return

    if not messages_to_summarize:
        await message.reply(f"За последние 12 часов в чате {chat_name} нихуя не было.")
        return

    logging.info(f"Found {len(messages_to_summarize)} messages to summarize in chat {chat_id}")
    logging.info(f"Users in period: {users_in_period}")

    summary_input_text = f"Сообщения из чата {chat_name} за последние 12 часов (всего {len(messages_to_summarize)} сообщений):\n\n"
    for msg in messages_to_summarize:
        summary_input_text += f"{msg['display_name']}: {msg['text']}\n"

    user_mentions_list = []
    for user_data in users_in_period.values():
        display_name = user_data["display_name"] 
        if display_name:
            user_mentions_list.append(display_name)
    
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "участников"

    summary_prompt = f"""Просуммируй следующие сообщения из чата {chat_name}. Сделай краткое изложение в свободной форме (с сарказмом и обсценной лексикой), разбей на абзацы. Не более 200 слов. 
    Упомяни участников беседы по имени (без символа @): {user_mentions_str}.
    Если обсуждения были незначительными или короткими, просто укажи основные темы и кто участвовал.
    Если сообщений мало, можно сделать совсем короткую сводку в один абзац.

    Вот сообщения для суммаризации:
    {summary_input_text}

    Суммаризация:
    """

    try:
        # Send typing/processing action
        random_action = random.choice(action_list)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)

        # Use the provided chat_model (gemini-2.0-flash)
        def sync_gemini_summarize_call():
            try:
                response = chat_model.generate_content(summary_prompt)
                return response.text  # Access the generated text
            except Exception as e:
                logging.error(f"Error generating content with model: {e}")
                return f"Ошибка при генерации ответа: {str(e)}"

        await message.reply("Пишу доклад...")
        summary_response = await asyncio.to_thread(sync_gemini_summarize_call)

        # Send the summary response
        await message.reply(summary_response)

    except Exception as e:
        logging.error(f"API Error during summarization: {e}")
        await message.reply("🤖 Не удалось просуммировать сообщения за последние 24 часа из-за ошибки API.")
