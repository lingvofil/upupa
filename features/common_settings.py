import base64
import logging
from aiogram import types
from core.loader import bot
from core.state import chat_list
from features.chat_settings import remove_chat
from features.group_bans import ban_group

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
    identifier = chat_identifier.strip()
    lookup = identifier
    if lookup.startswith(("https://t.me/", "http://t.me/")):
        lookup = "@" + lookup.rstrip("/").rsplit("/", 1)[-1]

    chat_info = None
    # Public @username / t.me links can be resolved directly by Telegram even
    # when the command is issued from another chat.
    if lookup.startswith("@"):
        try:
            chat_info = await bot.get_chat(lookup)
        except Exception:
            chat_info = None

    if chat_info is None:
        for chat in list(chat_list):
            if (identifier.startswith("-") and identifier[1:].isdigit() and chat["id"] == int(identifier)) or \
               (chat.get("title") and chat["title"].casefold() == identifier.casefold()) or \
               (chat.get("username") and chat["username"].casefold() == identifier.lstrip("@").casefold()):
                try:
                    chat_info = await bot.get_chat(chat["id"])
                except Exception:
                    chat_info = types.SimpleNamespace(
                        id=chat["id"], title=chat.get("title"), username=chat.get("username")
                    )
                break

    if chat_info is None:
        await message.reply("Не понимаю а чем реч")
        return

    chat_id = chat_info.id
    title = getattr(chat_info, "title", None)
    username = getattr(chat_info, "username", None)
    try:
        # Ban is written before leaving, so a transient leave/update race cannot
        # allow the group to be registered again.
        ban_group(chat_id, title, username)
        await bot.leave_chat(chat_id)
        remove_chat(chat_id)
        await message.reply(f"Ладно, нахуй {title or identifier}")
        logging.info("Упупа покинул и забанил чат: %s (%s)", title or identifier, chat_id)
    except Exception as e:
        logging.error(f"Ошибка при выходе из чата {chat_identifier}: {e}")
        await message.reply("Не понимаю а чем реч")

# Функция выхода из чатов с одним пользователем
async def process_leave_empty_chats(message: types.Message):
    """Выходит из всех групповых чатов, где только бот и один пользователь"""
    
    logging.info(f"Начинаю проверку чатов на одиночество. Всего чатов: {len(chat_list)}")
    
    left_chats = []
    failed_chats = []
    
    for chat in list(chat_list):
        chat_id = chat["id"]
        
        # Пропускаем личные чаты (они всегда 1 на 1)
        # ID личных чатов обычно положительные
        if chat_id > 0:
            continue
        
        try:
            # Получаем количество участников
            await bot.get_chat(chat_id)
            member_count = await bot.get_chat_member_count(chat_id)
            
            # Если в чате только 2 участника (бот + 1 человек)
            if member_count == 2:
                await bot.leave_chat(chat_id)
                remove_chat(chat_id)
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
