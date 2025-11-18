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
    else:
        # Если введено название чата
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

# Функция выхода из чатов с одним пользователем
async def process_leave_empty_chats(message: types.Message):
    """Выходит из всех групповых чатов, где только бот и один пользователь"""
    
    logging.info(f"Начинаю проверку чатов на одиночество. Всего чатов: {len(chat_list)}")
    
    left_chats = []
    failed_chats = []
    
    for chat in chat_list:
        chat_id = chat["id"]
        
        # Пропускаем личные чаты (они всегда 1 на 1)
        # ID личных чатов обычно положительные
        if chat_id > 0:
            continue
        
        try:
            # Получаем количество участников
            chat_info = await bot.get_chat(chat_id)
            member_count = await bot.get_chat_member_count(chat_id)
            
            # Если в чате только 2 участника (бот + 1 человек)
            if member_count == 2:
                await bot.leave_chat(chat_id)
                chat_title = chat.get("title", f"ID: {chat_id}")
                left_chats.append(chat_title)
                logging.info(f"Упупа покинул чат с одним пользователем: {chat_title} ({chat_id})")
        
        except Exception as e:
            error_str = str(e)
            # Если бот уже не в чате или чат не найден - это нормально, пропускаем
            if "bot was kicked" in error_str or "chat not found" in error_str:
                continue
            
            chat_title = chat.get("title", f"ID: {chat_id}")
            failed_chats.append(f"{chat_title}: {error_str}")
            logging.error(f"Ошибка при проверке/выходе из чата {chat_id}: {e}")
    
    # Формируем ответное сообщение
    if not left_chats and not failed_chats:
        response = "Нет чатов, где я остался только с одним хуесосом."
    else:
        response_parts = []
        
        if left_chats:
            response_parts.append(f"Вышел из {len(left_chats)} чат(ов):\n" + "\n".join([f"- {title}" for title in left_chats]))
        
        if failed_chats:
            response_parts.append(f"\n\nНе удалось выйти из {len(failed_chats)} чат(ов):\n" + "\n".join([f"- {title}" for title in failed_chats]))
        
        response = "\n".join(response_parts)
    
    await message.reply(response)
