import os
import json
import logging
from aiogram import types, Bot
from config import CHAT_SETTINGS_FILE, CHAT_LIST_FILE, SPECIAL_CHAT_ID, chat_settings, chat_list, ADMIN_ID

# Функция загрузки настроек чатов при старте
def load_chat_settings():
    global chat_settings
    if os.path.exists(CHAT_SETTINGS_FILE):
        try:
            with open(CHAT_SETTINGS_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, dict):  # Проверяем, что файл содержит словарь
                    chat_settings = data
                    logging.info(f"Загружены настройки для {len(chat_settings)} чатов.")
                else:
                    chat_settings = {}
                    logging.warning("Файл chat_settings.json повреждён, создан новый.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке настроек чатов: {e}")
            chat_settings = {}
    else:
        chat_settings = {}

# Функция сохранения настроек чатов в файл
def save_chat_settings():
    try:
        with open(CHAT_SETTINGS_FILE, "w", encoding="utf-8") as file:
            json.dump(chat_settings, file, ensure_ascii=False, indent=4)
        logging.info("Настройки чатов сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении настроек чатов: {e}")

load_chat_settings() # Загружаем настройки при старте бота


# Функция загрузки списка чатов при старте
def load_chats():
    global chat_list
    if os.path.exists(CHAT_LIST_FILE):
        try:
            with open(CHAT_LIST_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, list):  # Проверяем, что файл содержит список
                    # ИЗМЕНЕНО: Модифицируем список на месте, а не переназначаем
                    chat_list.clear()
                    chat_list.extend(data)
                    logging.info(f"Загружено {len(chat_list)} чатов из файла.")
                else:
                    chat_list.clear()
                    logging.warning("Файл chats.json повреждён, создан новый список чатов.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке списка чатов: {e}")
            chat_list.clear()
    else:
        chat_list.clear()

# Функция сохранения списка чатов в файл
def save_chats():
    try:
        with open(CHAT_LIST_FILE, "w", encoding="utf-8") as file:
            json.dump(chat_list, file, ensure_ascii=False, indent=4)
        logging.info("Список чатов сохранён.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении списка чатов: {e}")

# Функция добавления чата (без дублирования)
def add_chat(chat_id, chat_title, chat_username=None):
    global chat_list

    # Проверяем, есть ли уже этот чат в списке
    if not any(chat["id"] == chat_id for chat in chat_list):
        chat_info = {
            "id": chat_id,
            "title": chat_title,
            "username": chat_username if chat_username else None
        }
        chat_list.append(chat_info)
        save_chats()  # Сохраняем изменения
        logging.info(f"Добавлен новый чат: {chat_title} ({chat_id})")

    # Перемещаем "особый" чат наверх
    chat_list.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
        
# Загружаем список чатов при старте бота
load_chats()

# Улучшенная функция получения списка чатов
def get_chats_list(chat_id, chat_title, chat_username):
    # Добавляем текущий чат, если его нет
    add_chat(chat_id, chat_title, chat_username)
    
    # Фильтруем чаты без названия (где title == None)
    filtered_chats = [chat for chat in chat_list if chat.get("title")]
    if not filtered_chats:
        return "Я пока никуда не добавлен."

    # Сортируем список перед созданием нумерованного текста
    filtered_chats.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
    
    # Создаем новый список с правильной нумерацией
    numbered_chats = []
    for i, chat in enumerate(filtered_chats):
        if chat.get("username"):
            numbered_chats.append(f"{i+1}. {chat['title']} (@{chat['username']})")
        else:
            numbered_chats.append(f"{i+1}. {chat['title']}")
    
    response = "Тут:\n" + "\n".join(numbered_chats)
    return response

async def process_update_all_chats(message: types.Message, bot: Bot):
    """Попытка обновить информацию о всех чатах через API бота и удаление недоступных чатов"""
    global chat_list
    
    if message.from_user.id != ADMIN_ID:  # Проверка на админа
        await message.reply("Иди нахуй, у тебя нет прав на это.")
        return
        
    try:
        updated_chats = []
        successful_updates = 0
        removed_chats = []
        
        for chat in chat_list:
            try:
                # Получаем актуальную информацию о чате через API
                chat_info = await bot.get_chat(chat["id"])
                
                # Обновляем информацию
                updated_chat = {
                    "id": chat["id"],
                    "title": chat_info.title,
                    "username": chat_info.username
                }
                updated_chats.append(updated_chat)
                successful_updates += 1
            except Exception as e:
                error_str = str(e)
                # Проверяем, если ошибка связана с исключением бота из чата
                if "bot was kicked" in error_str or "bot was blocked" in error_str or "chat not found" in error_str:
                    removed_chats.append({
                        "id": chat["id"],
                        "title": chat.get("title", "Неизвестный чат"),
                        "reason": error_str
                    })
                    logging.info(f"Удален чат {chat.get('title', chat['id'])}: бот был исключен или забанен")
                else:
                    # Другие ошибки - сохраняем старую информацию о чате
                    logging.warning(f"Не удалось обновить информацию о чате {chat['id']}: {e}")
                    updated_chats.append(chat)
        
        # Добавляем текущий чат, если его нет в списке
        current_chat_id = message.chat.id
        if not any(chat["id"] == current_chat_id for chat in updated_chats):
            updated_chats.append({
                "id": current_chat_id,
                "title": message.chat.title,
                "username": message.chat.username
            })
        
        # Удаляем дубликаты и чаты без названия
        unique_chats = []
        unique_ids = set()
        for chat in updated_chats:
            if chat["id"] not in unique_ids and chat.get("title"):
                unique_ids.add(chat["id"])
                unique_chats.append(chat)
        
        # ИЗМЕНЕНО: Обновляем глобальный список на месте
        chat_list.clear()
        chat_list.extend(unique_chats)
        
        # Сортируем чаты (специальный чат всегда первый)
        chat_list.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
        
        # Обновляем индексы для всех чатов
        for i, chat in enumerate(chat_list):
            chat["index"] = i + 1
        
        save_chats()  # Сохраняем обновленный список
        
        # Формируем отчет об удаленных чатах
        removed_info = ""
        if removed_chats:
            removed_info = "\n\nУдаленные чаты:\n" + "\n".join([
                f"- {chat['title']} (ID: {chat['id']})" 
                for chat in removed_chats
            ])
        
        # Формируем сообщение с результатами
        result_message = (
            f"Список чатов обновлен.\n"
            f"Успешно обновлено: {successful_updates}\n"
            f"Удалено чатов: {len(removed_chats)}"
            f"{removed_info}"
        )
        
        await message.reply(result_message)
        
    except Exception as e:
        logging.error(f"Ошибка при полном обновлении списка чатов: {e}")
        await message.reply("Произошла ошибка при обновлении списка чатов.")
