import json
import re
import asyncio
import random
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from aiogram import types
from aiogram.types import Message
import logging
from Config import model, LOG_FILE, ADMIN_ID

# Файл для хранения дней рождения
BIRTHDAY_FILE = "birthdays.json"

def load_birthdays() -> Dict:
    """Загрузка дней рождения из файла"""
    try:
        with open(BIRTHDAY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.error(f"Ошибка чтения файла {BIRTHDAY_FILE}")
        return {}

def save_birthdays(birthdays: Dict) -> None:
    """Сохранение дней рождения в файл"""
    try:
        with open(BIRTHDAY_FILE, 'w', encoding='utf-8') as f:
            json.dump(birthdays, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения файла {BIRTHDAY_FILE}: {e}")

def get_chat_birthdays(chat_id: int) -> Dict:
    """Получение дней рождения для конкретного чата"""
    all_birthdays = load_birthdays()
    chat_key = str(chat_id)
    return all_birthdays.get(chat_key, {})

def save_chat_birthday(chat_id: int, user_id: int, birthday_data: Dict) -> None:
    """Сохранение дня рождения пользователя в конкретном чате"""
    all_birthdays = load_birthdays()
    chat_key = str(chat_id)
    user_key = str(user_id)
    
    if chat_key not in all_birthdays:
        all_birthdays[chat_key] = {}
    
    all_birthdays[chat_key][user_key] = birthday_data
    save_birthdays(all_birthdays)

def parse_birthday_date(text: str) -> Optional[Tuple[int, int]]:
    """Парсинг даты дня рождения из текста"""
    # Поиск паттерна "мой др DD месяц" или "мой др DD.MM"
    patterns = [
        r"мой др (\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
        r"мой др (\d{1,2})\.(\d{1,2})",
        r"мой др (\d{1,2})/(\d{1,2})"
    ]
    
    months = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
    }
    
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            day = int(match.group(1))
            if len(match.groups()) > 1:
                if match.group(2) in months:
                    month = months[match.group(2)]
                else:
                    month = int(match.group(2))
            else:
                continue
            
            # Валидация даты
            if 1 <= day <= 31 and 1 <= month <= 12:
                return (day, month)
    
    return None

def get_user_messages_from_log(user_id: int, chat_id: int, limit: int = 100) -> List[str]:
    """Получение случайных сообщений пользователя из лога конкретного чата"""
    messages = []
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Ищем все сообщения пользователя в конкретном чате
        user_messages = []
        for line in lines:
            if f"Chat {chat_id}" in line and f"User {user_id}" in line and "]:" in line:
                # Извлекаем текст сообщения после "]:"
                message_text = line.split("]:")[1].strip()
                if message_text and len(message_text) > 10:  # Игнорируем очень короткие сообщения
                    user_messages.append(message_text)
        
        # Если сообщений больше лимита, берем случайные
        if len(user_messages) > limit:
            messages = random.sample(user_messages, limit)
        else:
            messages = user_messages
                        
    except Exception as e:
        logging.error(f"Ошибка чтения лога: {e}")
    
    return messages

async def generate_birthday_greeting(user_name: str, user_messages: List[str]) -> str:
    """Генерация саркастичного поздравления с использованием Gemini"""
    
    # Создаем контекст из сообщений пользователя
    messages_context = "\n".join(user_messages[:80])  # Берем 80 сообщений для контекста
    
    prompt = f"""
    Создай саркастичное, но веселое поздравление с днем рождения для пользователя {user_name}.
    
    Контекст его сообщений из чата:
    {messages_context}
    
    Требования:
    - Максимально саркастичное и ироничное
    - Обилие нецензурной лексики (мат приветствуется)
    - Веселое и дружелюбное по сути
    - Ссылки на его сообщения/поведение в чате
    - ОБЯЗАТЕЛЬНО включи пожелания (что желаешь в новом году жизни)
    - Не более 250 слов
    - Начинай с "С днюхой" или похожего
    - Используй стиль токсичного, но любящего друга
    - Обязательно добавь конкретные пожелания в конце
    
    Пример стиля: "С днюхой, ублюдок! Еще один год твоего пиздеца в этом чате прошел... Желаю тебе в новом году жизни..."
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Ошибка генерации поздравления: {e}")
        return f"С днюхой, {user_name}! Бот сломался, но поздравить тебя не забыл, ублюдок! Желаю тебе в новом году меньше багов и больше радости! 🎉"

async def handle_birthday_command(message: Message):
    """Обработка команды запоминания дня рождения"""
    try:
        # Парсинг даты
        birthday_date = parse_birthday_date(message.text)
        if not birthday_date:
            await message.reply("Да пошел ты нахуй, пиши нормально: 'упупа запомни: мой др 1 апреля'")
            return
        
        day, month = birthday_date
        
        # Подготовка данных пользователя
        birthday_data = {
            "day": day,
            "month": month,
            "name": message.from_user.first_name or "",
            "username": message.from_user.username or "",
        }
        
        # Сохранение в конкретном чате
        save_chat_birthday(message.chat.id, message.from_user.id, birthday_data)
        
        await message.reply("Записал в календарике этого чата 📅")
        
    except Exception as e:
        logging.error(f"Ошибка в handle_birthday_command: {e}")
        await message.reply("Что-то пошло не так, попробуй еще раз")

async def check_birthdays_and_send_greetings(bot):
    """Проверка дней рождения и отправка поздравлений"""
    try:
        now = datetime.now()
        current_day = now.day
        current_month = now.month
        today_str = now.strftime("%Y-%m-%d")
        
        all_birthdays = load_birthdays()
        
        # Проходим по всем чатам
        for chat_id, chat_birthdays in all_birthdays.items():
            chat_id_int = int(chat_id)
            
            # Проходим по всем пользователям в чате
            for user_id, user_data in chat_birthdays.items():
                if user_data["day"] == current_day and user_data["month"] == current_month:
                    # Проверяем, не поздравляли ли уже сегодня
                    last_greeting_key = f"last_greeting_{today_str}"
                    
                    if user_data.get(last_greeting_key):
                        continue  # Уже поздравляли сегодня
                    
                    # Получаем сообщения пользователя из конкретного чата
                    user_messages = get_user_messages_from_log(int(user_id), chat_id_int)
                    
                    if not user_messages:
                        greeting = f"С днюхой, {user_data['name']}! Хоть сообщений от тебя и нет, но поздравить забыть не могу, ублюдок! Желаю тебе в новом году больше активности в чате! 🎉"
                    else:
                        greeting = await generate_birthday_greeting(user_data['name'], user_messages)
                    
                    # Создаем тег пользователя
                    user_tag = f"[{user_data['name']}](tg://user?id={user_id})"
                    
                    # Отправляем поздравление в чат
                    final_greeting = f"{user_tag}\n\n{greeting}"
                    await bot.send_message(chat_id_int, final_greeting, parse_mode="Markdown")
                    
                    # Помечаем, что поздравили сегодня
                    user_data[last_greeting_key] = True
        
        # Сохраняем обновленные данные
        save_birthdays(all_birthdays)
        
    except Exception as e:
        logging.error(f"Ошибка в check_birthdays_and_send_greetings: {e}")

async def birthday_scheduler(bot):
    """Планировщик проверки дней рождения"""
    while True:
        try:
            now = datetime.now()
            # Проверяем каждый день в 12:00
            if now.hour == 12 and now.minute == 0:
                await check_birthdays_and_send_greetings(bot)
                # Ждем минуту, чтобы не выполнять несколько раз
                await asyncio.sleep(60)
            else:
                # Проверяем каждую минуту
                await asyncio.sleep(60)
        except Exception as e:
            logging.error(f"Ошибка в birthday_scheduler: {e}")
            await asyncio.sleep(60)

def get_birthday_list(chat_id: int) -> str:
    """Получение списка дней рождения для конкретного чата"""
    chat_birthdays = get_chat_birthdays(chat_id)
    
    if not chat_birthdays:
        return "В этом чате дни рождения не записаны"
    
    result = "📅 Дни рождения в этом чате:\n\n"
    for user_id, data in chat_birthdays.items():
        name = data.get('name', 'Неизвестно')
        username = data.get('username', '')
        day = data['day']
        month = data['month']
        
        month_names = ["", "января", "февраля", "марта", "апреля", "мая", "июня", 
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        
        result += f"{name}"
        if username:
            result += f" (@{username})"
        result += f" - {day} {month_names[month]}\n"
    
    return result

def get_all_birthdays_list() -> str:
    """Получение списка всех дней рождения из всех чатов (только для админа)"""
    all_birthdays = load_birthdays()
    
    if not all_birthdays:
        return "Дни рождения не записаны"
    
    result = "📅 Все записанные дни рождения:\n\n"
    
    for chat_id, chat_birthdays in all_birthdays.items():
        result += f"🔹 Чат {chat_id}:\n"
        
        for user_id, data in chat_birthdays.items():
            name = data.get('name', 'Неизвестно')
            username = data.get('username', '')
            day = data['day']
            month = data['month']
            
            month_names = ["", "января", "февраля", "марта", "апреля", "мая", "июня", 
                          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            
            result += f"   {name}"
            if username:
                result += f" (@{username})"
            result += f" - {day} {month_names[month]}\n"
        
        result += "\n"
    
    return result

def find_user_in_chat_birthdays(chat_id: int, identifier: str) -> Optional[Tuple[str, Dict]]:
    """Поиск пользователя в базе дней рождения конкретного чата по имени или username"""
    chat_birthdays = get_chat_birthdays(chat_id)
    
    for user_id, user_data in chat_birthdays.items():
        name = user_data.get('name', '').lower()
        username = user_data.get('username', '').lower()
        
        if (identifier.lower() == name or 
            identifier.lower() == username or 
            identifier.lower() == f"@{username}"):
            return user_id, user_data
    
    return None

async def handle_test_greeting_command(message: Message):
    """Обработка тестовой команды поздравления"""
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        # Извлекаем имя/username из команды
        text = message.text
        if text.lower().startswith("упупа поздравь "):
            identifier = text[15:].strip()  # Убираем "упупа поздравь "
        elif text.lower().startswith("поздравь "):
            identifier = text[9:].strip()  # Убираем "поздравь "
        else:
            await message.reply("Используй: упупа поздравь [имя/username]")
            return
            
        # Ищем пользователя в текущем чате
        user_info = find_user_in_chat_birthdays(message.chat.id, identifier)
        if not user_info:
            await message.reply(f"Пользователь '{identifier}' не найден в базе дней рождения этого чата")
            return
        
        user_id, user_data = user_info
        
        # Получаем сообщения пользователя из текущего чата
        user_messages = get_user_messages_from_log(int(user_id), message.chat.id)
        
        if not user_messages:
            greeting = f"С днюхой, {user_data['name']}! Хоть сообщений от тебя и нет, но поздравить забыть не могу, ублюдок! Желаю тебе в новом году больше активности в чате! 🎉"
        else:
            greeting = await generate_birthday_greeting(user_data['name'], user_messages)
        
        # Создаем тег пользователя
        user_tag = f"[{user_data['name']}](tg://user?id={user_id})"
        
        # Отправляем тестовое поздравление
        test_message = f"🧪 **ТЕСТОВОЕ ПОЗДРАВЛЕНИЕ** 🧪\n\n{user_tag}\n\n{greeting}"
        await message.reply(test_message, parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Ошибка в handle_test_greeting_command: {e}")
        await message.reply("Ошибка при генерации тестового поздравления")

async def handle_birthday_list_command(message: Message):
    """Обработка команды просмотра списка дней рождения для текущего чата"""
    birthday_list = get_birthday_list(message.chat.id)
    await message.reply(birthday_list)

async def handle_admin_birthday_list_command(message: Message):
    """Обработка команды просмотра всех дней рождения (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Только для админа")
        return
    
    birthday_list = get_all_birthdays_list()
    await message.reply(birthday_list)