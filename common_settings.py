import base64
import logging
from aiogram import types
from config import chat_list, bot

# Функция для обрезки истории разговора
def trim_history(history, max_length=4096):
    current_length = sum(len(message["content"]) for message in history)
    while history and current_length > max_length:
        removed_message = history.pop(0)
        current_length -= len(removed_message["content"])
    return history

# Функция для кодирования файла в base64
def encode_file_to_base64(file_path):
    with open(file_path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")

# Функция выхода из чатов
async def process_leave_chat(message: types.Message, chat_identifier: str):
    # Если введён ID чата
    if chat_identifier.startswith("-") and chat_identifier[1:].isdigit():
        chat_id = int(chat_identifier)
    else:  # Если введено название чата
        chat_id = None
        for chat in chat_list:
            # Проверяем название и username (если есть)
            if (chat["title"] and chat["title"].lower() == chat_identifier.lower()) or \
               (chat.get("username") and chat["username"].lower() == chat_identifier.lower().strip('@')):
                chat_id = chat["id"]
                break
    
    if not chat_id:
        await message.reply("Не понимаю а чем реч")
        return
    
    try:
        await bot.leave_chat(chat_id)
        await message.reply(f"Ладно, нахуй {chat_identifier}")
        logging.info(f"Упупа покинул чат: {chat_identifier} ({chat_id})")
    except Exception as e:
        logging.error(f"Ошибка при выходе из чата {chat_identifier}: {e}")
        await message.reply("Не понимаю а чем реч")
