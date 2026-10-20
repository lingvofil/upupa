#birthday_calendar.py

import html
import re
import asyncio
import random
import threading
from datetime import datetime
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
import logging
import traceback
from core.json_repository import JsonFileRepository
from core.paths import BIRTHDAYS_PATH, USER_MESSAGES_LOG_PATH as LOG_FILE
from core.history_store import get_history_repository
from core.settings import ADMIN_ID
from core.state import chat_settings
from core.telegram_markup import is_entity_parse_error
from infrastructure.ai.clients import gigachat_model, groq_ai, model

# Файл для хранения дней рождения
BIRTHDAY_FILE = str(BIRTHDAYS_PATH)

# Все изменения файла идут через update_birthdays: чтение свежей версии,
# правка и атомарная запись под одной блокировкой, без устаревших снимков.
_birthdays_lock = threading.Lock()


class BirthdayStorageError(RuntimeError):
    """Файл дней рождения повреждён — перезаписывать его нельзя."""


def _read_birthdays() -> Dict:
    try:
        data = JsonFileRepository(BIRTHDAY_FILE).load()
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise BirthdayStorageError(f"Не удалось прочитать {BIRTHDAY_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise BirthdayStorageError(
            f"{BIRTHDAY_FILE}: ожидался JSON-объект, получен {type(data).__name__}"
        )
    return data


def load_birthdays() -> Dict:
    """Загрузка дней рождения для чтения (повреждённый файл не трогаем)."""
    try:
        return _read_birthdays()
    except BirthdayStorageError as exc:
        logging.error("%s", exc)
        return {}

def save_birthdays(birthdays: Dict) -> None:
    """Атомарное сохранение дней рождения в файл"""
    JsonFileRepository(BIRTHDAY_FILE, indent=2).save(birthdays)


def update_birthdays(mutator: Callable[[Dict], Any]) -> Any:
    """Изменить актуальное содержимое файла и атомарно сохранить его.

    Повреждённый файл не заменяется пустым: BirthdayStorageError уходит вызывающему.
    """
    with _birthdays_lock:
        birthdays = _read_birthdays()
        result = mutator(birthdays)
        save_birthdays(birthdays)
        return result

def get_chat_birthdays(chat_id: int) -> Dict:
    """Получение дней рождения для конкретного чата"""
    all_birthdays = load_birthdays()
    chat_key = str(chat_id)
    return all_birthdays.get(chat_key, {})

def save_chat_birthday(chat_id: int, user_id: int, birthday_data: Dict) -> None:
    """Сохранение дня рождения пользователя в конкретном чате"""
    def apply(all_birthdays: Dict) -> None:
        all_birthdays.setdefault(str(chat_id), {})[str(user_id)] = birthday_data

    update_birthdays(apply)


def _mark_greeted(chat_id: str, user_id: str, greeting_key: str) -> None:
    """Отметить отправленное поздравление в свежей версии файла."""
    def apply(all_birthdays: Dict) -> None:
        user_data = all_birthdays.get(chat_id, {}).get(user_id)
        # Запись могли удалить или перезаписать, пока шла генерация.
        if isinstance(user_data, dict):
            user_data[greeting_key] = True

    update_birthdays(apply)


def _greeting_texts(user_id, user_name: str, greeting: str, *, test: bool = False) -> tuple[str, str]:
    """Вернуть Markdown-вариант поздравления и HTML-вариант на случай ошибки разметки."""
    markdown_header = "🧪 **ПОЗДРАВЛЕНИЕ** 🧪\n\n" if test else ""
    html_header = "🧪 <b>ПОЗДРАВЛЕНИЕ</b> 🧪\n\n" if test else ""
    markdown_text = f"{markdown_header}[{user_name}](tg://user?id={user_id})\n\n{greeting}"
    html_text = (
        f'{html_header}<a href="tg://user?id={int(user_id)}">{html.escape(user_name or "именинник")}</a>'
        f"\n\n{html.escape(greeting)}"
    )
    return markdown_text, html_text


async def _send_greeting(send, user_id, user_name: str, greeting: str, *, test: bool = False):
    """Отправить поздравление; имя или ответ модели не должны ломать доставку."""
    markdown_text, html_text = _greeting_texts(user_id, user_name, greeting, test=test)
    try:
        return await send(markdown_text, parse_mode="Markdown")
    except TelegramBadRequest as exc:
        if not is_entity_parse_error(exc):
            raise
        logging.warning("Поздравление не прошло разметку Markdown, отправляю HTML: %s", exc)
    return await send(html_text, parse_mode="HTML")

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
    repository = get_history_repository(LOG_FILE)
    if repository is not None:
        return [row["text"].strip() for row in repository.select(
            chat_id, user_id=user_id, sample_size=max(0, limit), min_chars=11)]
    """Получение случайных сообщений пользователя из лога конкретного чата."""
    if limit <= 0:
        return []

    # Reservoir sampling сохраняет равновероятную случайную выборку, но в
    # памяти одновременно находится не более limit сообщений пользователя.
    messages: List[str] = []
    matching_count = 0
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if f"Chat {chat_id}" not in line or f"User {user_id}" not in line or "]:" not in line:
                    continue

                message_text = line.split("]:")[1].strip()
                if not message_text or len(message_text) <= 10:
                    continue

                matching_count += 1
                if len(messages) < limit:
                    messages.append(message_text)
                    continue

                replacement_index = random.randrange(matching_count)
                if replacement_index < limit:
                    messages[replacement_index] = message_text

    except Exception as e:
        logging.error(f"Ошибка чтения лога: {e}")

    return messages

async def generate_birthday_greeting(user_name: str, user_messages: List[str], chat_id: int) -> str:
    """Генерация саркастичного поздравления с использованием выбранной модели"""
    messages_context = "\n".join(user_messages[:80])

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
        # Получаем активную модель для чата
        chat_key = str(chat_id)
        current_settings = chat_settings.get(chat_key, {})
        active_model = current_settings.get("active_model", "gemini")

        # Режим истории не подходит для генерации поздравлений
        if active_model == "history":
            active_model = "gemini"

        logging.info(f"Генерация поздравления для {user_name} с моделью {active_model}")

        # Генерируем через выбранную модель. Провайдеры синхронные: вне потока
        # они остановили бы event loop на всё время генерации.
        if active_model == "gigachat":
            response = await asyncio.to_thread(gigachat_model.generate_content, prompt, chat_id=chat_id)
            return response.text.strip()
        elif active_model == "groq":
            return await asyncio.to_thread(groq_ai.generate_text, prompt)
        else:  # gemini
            response = await asyncio.to_thread(model.generate_content, prompt, chat_id=chat_id)
            return response.text.strip()

    except Exception as e:
        logging.error(f"Ошибка генерации поздравления: {e}")
        return f"С днюхой, {user_name}! Бот сломался, но поздравить тебя не забыл, ублюдок! Желаю тебе в новом году меньше багов и больше радости! 🎉"

async def handle_birthday_command(message: Message):
    """Обработка команды запоминания дня рождения"""
    try:
        birthday_date = parse_birthday_date(message.text)
        if not birthday_date:
            await message.reply("Да пошел ты нахуй, пиши нормально: 'упупа запомни: мой др 1 апреля'")
            return

        day, month = birthday_date

        birthday_data = {
            "day": day,
            "month": month,
            "name": message.from_user.first_name or "",
            "username": message.from_user.username or "",
        }

        await asyncio.to_thread(save_chat_birthday, message.chat.id, message.from_user.id, birthday_data)

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

        logging.info(f"Проверка дней рождения на {today_str}")

        # Снимок нужен только для поиска именинников. Отметки о поздравлении
        # пишутся в свежую версию файла: ДР, добавленные во время генерации, не теряются.
        all_birthdays = await asyncio.to_thread(load_birthdays)

        if not all_birthdays:
            logging.info("Нет записанных дней рождения")
            return

        for chat_id, chat_birthdays in all_birthdays.items():
            try:
                chat_id_int = int(chat_id)

                if not isinstance(chat_birthdays, dict):
                    logging.error(f"Неверный формат данных для чата {chat_id}: {type(chat_birthdays)}")
                    continue

                for user_id, user_data in chat_birthdays.items():
                    try:
                        if not isinstance(user_data, dict):
                            logging.error(f"Неверный формат данных для пользователя {user_id} в чате {chat_id}: {type(user_data)}")
                            continue

                        if "day" not in user_data or "month" not in user_data:
                            logging.error(f"Отсутствуют обязательные поля для пользователя {user_id} в чате {chat_id}: {user_data}")
                            continue

                        user_day = user_data["day"]
                        user_month = user_data["month"]

                        if not isinstance(user_day, int) or not isinstance(user_month, int):
                            logging.error(f"Неверный формат даты для пользователя {user_id} в чате {chat_id}: day={user_day}, month={user_month}")
                            continue

                        if user_day == current_day and user_month == current_month:
                            last_greeting_key = f"last_greeting_{today_str}"

                            if user_data.get(last_greeting_key):
                                logging.info(f"Пользователь {user_id} в чате {chat_id} уже поздравлен сегодня")
                                continue

                            logging.info(f"Поздравляем пользователя {user_id} в чате {chat_id}")

                            user_messages = await asyncio.to_thread(get_user_messages_from_log, int(user_id), chat_id_int)
                            user_name = user_data.get('name', 'Неизвестный')

                            if not user_messages:
                                greeting = f"С днюхой, {user_name}! Хоть сообщений от тебя и нет, но поздравить забыть не могу, ублюдок! Желаю тебе в новом году больше активности в чате! 🎉"
                            else:
                                greeting = await generate_birthday_greeting(
                                    user_name=user_name,
                                    user_messages=user_messages,
                                    chat_id=chat_id_int
                                )

                            try:
                                await _send_greeting(
                                    partial(bot.send_message, chat_id_int),
                                    user_id,
                                    user_name,
                                    greeting,
                                )
                                logging.info(f"Поздравление отправлено в чат {chat_id} для пользователя {user_id}")
                            except Exception as e:
                                logging.error(f"Ошибка отправки поздравления в чат {chat_id}: {e}")
                                continue

                            try:
                                await asyncio.to_thread(_mark_greeted, chat_id, user_id, last_greeting_key)
                            except Exception as e:
                                logging.error(f"Не удалось отметить поздравление {user_id} в чате {chat_id}: {e}")

                    except Exception as e:
                        logging.error(f"Ошибка обработки пользователя {user_id} в чате {chat_id}: {e}")
                        continue

            except Exception as e:
                logging.error(f"Ошибка обработки чата {chat_id}: {e}")
                continue

    except Exception as e:
        logging.error(f"Ошибка в check_birthdays_and_send_greetings: {e}")
        logging.error(f"Traceback: {traceback.format_exc()}")

async def birthday_scheduler(bot):
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            today = now.date()

            if now.hour == 12 and last_run_date != today:
                await check_birthdays_and_send_greetings(bot)
                last_run_date = today

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка в birthday_scheduler: {e}")
            await asyncio.sleep(60)

def get_birthday_list(chat_id: int) -> str:
    """Получение списка дней рождения для конкретного чата (отсортировано по дате)"""
    chat_birthdays = get_chat_birthdays(chat_id)

    if not chat_birthdays:
        return "В этом чате дни рождения не записаны"

    # Сортируем дни рождения по месяцу и дню
    sorted_birthdays = sorted(chat_birthdays.items(), key=lambda item: (item[1]['month'], item[1]['day']))

    result = "📅 Дни рождения в этом чате:\n\n"
    month_names = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    for user_id, data in sorted_birthdays:
        name = data.get('name', 'Неизвестно')
        username = data.get('username', '')
        day = data['day']
        month = data['month']

        result += f"{name}"
        if username:
            result += f" (@{username})"
        result += f" - {day} {month_names[month]}\n"

    return result

def get_all_birthdays_list() -> str:
    """Получение списка всех дней рождения из всех чатов (только для админа, отсортировано)"""
    all_birthdays = load_birthdays()

    if not all_birthdays:
        return "Дни рождения не записаны"

    result = "📅 Все записанные дни рождения (отсортировано по дате в каждом чате):\n\n"
    month_names = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    # Сортируем чаты по ID для консистентности вывода
    sorted_chat_ids = sorted(all_birthdays.keys(), key=int)

    for chat_id in sorted_chat_ids:
        chat_birthdays = all_birthdays[chat_id]
        result += f"🔹 Чат {chat_id}:\n"

        # Сортируем дни рождения внутри чата по месяцу и дню
        sorted_birthdays = sorted(chat_birthdays.items(), key=lambda item: (item[1]['month'], item[1]['day']))

        if not sorted_birthdays:
            result += "   Нет записанных дней рождения\n"

        for user_id, data in sorted_birthdays:
            name = data.get('name', 'Неизвестно')
            username = data.get('username', '')
            day = data['day']
            month = data['month']

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
        text = message.text or ""

        if text.lower().startswith("упупа поздравь "):
            identifier = text[15:].strip()
        elif text.lower().startswith("поздравь "):
            identifier = text[9:].strip()
        else:
            await message.reply("Используй: упупа поздравь [имя/username]")
            return

        if not identifier:
            await message.reply("Укажи имя или username пользователя")
            return

        user_info = find_user_in_chat_birthdays(message.chat.id, identifier)
        if not user_info:
            await message.reply(
                f"Пользователь '{identifier}' не найден в базе дней рождения этого чата"
            )
            return

        user_id, user_data = user_info

        user_messages = await asyncio.to_thread(get_user_messages_from_log,
            int(user_id),
            message.chat.id
        )

        if not user_messages:
            greeting = (
                f"С днюхой, {user_data['name']}! "
                f"Хоть сообщений от тебя и нет, но поздравить забыть не могу, ублюдок! "
                f"Желаю тебе в новом году больше активности в чате! 🎉"
            )
        else:
            greeting = await generate_birthday_greeting(
                user_name=user_data["name"],
                user_messages=user_messages,
                chat_id=message.chat.id
            )

        await _send_greeting(message.reply, user_id, user_data["name"], greeting, test=True)

    except Exception:
        logging.exception("Ошибка в handle_test_greeting_command")
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
