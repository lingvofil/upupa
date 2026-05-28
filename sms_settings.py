import os
import json
import logging
import re
from collections import deque
from datetime import datetime, timedelta
from aiogram import Bot, types
# ИСПРАВЛЕНИЕ: Добавляем импорт `sms_disabled_chats` из config.py
from config import SMS_DISABLED_CHATS_FILE, SPECIAL_CHAT_ID, LOG_FILE, sms_disabled_chats


USER_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r" - Chat (?P<chat_id>-?\d+) \(.*?\)"
    r" - User \d+ \((?P<username>.*?)\) \[(?P<full_name>.*?)\]: (?P<text>.*)$",
    re.DOTALL,
)


def _get_numbered_chats(chat_list: list) -> list:
    """Возвращает список чатов с той же фильтрацией и сортировкой, что и команда "где сидишь"."""
    filtered_chats = [chat for chat in chat_list if chat.get("title")]
    filtered_chats.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
    return filtered_chats


def _parse_user_log_line(line: str):
    """Парсит строку user_messages.log и возвращает данные сообщения или None."""
    match = USER_LOG_LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None

    return match.groupdict()


def _iter_user_log_records(file):
    """Возвращает логические записи лога, склеивая многострочные сообщения."""
    current_record = None

    for line in file:
        if USER_LOG_LINE_RE.match(line):
            if current_record is not None:
                yield current_record
            current_record = line
        elif current_record is not None:
            current_record += line

    if current_record is not None:
        yield current_record


def _format_log_time(timestamp: str) -> str:
    try:
        return (datetime.fromisoformat(timestamp) + timedelta(hours=1)).strftime("%H:%M")
    except ValueError:
        return timestamp


def _format_log_author(username: str, full_name: str) -> str:
    if full_name and full_name != "NoName":
        return full_name
    if username and username != "NoUsername":
        return username
    return "Аноним"


def _parse_chat_index(raw_index: str) -> int:
    return int(raw_index.strip().lstrip("#")) - 1


async def _notify_peeked_chat(message: types.Message, target_chat_id: str, source_chat_title: str, bot: Bot | None):
    if bot is None or str(message.chat.id) == target_chat_id:
        return

    notification = (
        f"за вами подсматривают крысы из {source_chat_title}, "
        "вы тоже можете подсмотреть с помощью команды «чоговорят #чата»"
    )

    try:
        await bot.send_message(target_chat_id, notification)
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления о подсматривании в чат {target_chat_id}: {e}")


async def process_what_they_say(message: types.Message, chat_list: list, bot: Bot | None = None):
    """Отправляет последние 10 сохранённых сообщений из чата по номеру из команды "чоговорят"."""
    command_text = message.text or ""
    parts = command_text.split(maxsplit=1)

    if len(parts) < 2:
        await message.reply("Укажи номер чата: чоговорят <номер чата>")
        return

    try:
        chat_index = _parse_chat_index(parts[1])
    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: чоговорят <номер чата>")
        return

    filtered_chats = _get_numbered_chats(chat_list)
    if chat_index < 0 or chat_index >= len(filtered_chats):
        await message.reply("Чат с таким номером не найден, иди нахуй")
        return

    target_chat = filtered_chats[chat_index]
    target_chat_id = str(target_chat["id"])
    recent_messages = deque(maxlen=10)

    if not os.path.exists(LOG_FILE):
        await message.reply("Пока нечего рассказать: лог сообщений пуст.")
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as file:
            for record in _iter_user_log_records(file):
                parsed = _parse_user_log_line(record)
                if not parsed or parsed["chat_id"] != target_chat_id:
                    continue

                text = parsed["text"].strip().replace("\n", " / ")
                if not text:
                    continue

                recent_messages.append(
                    f"{_format_log_time(parsed['timestamp'])} "
                    f"{_format_log_author(parsed['username'], parsed['full_name'])}: {text}"
                )
    except Exception as e:
        logging.error(f"Ошибка при чтении последних сообщений чата {target_chat_id}: {e}")
        await message.reply("Не удалось прочитать сообщения. Возможно, я хуисос")
        return

    if not recent_messages:
        await message.reply(f"В чате {target_chat.get('title', target_chat_id)} пока нет сохранённых сообщений.")
        return

    await _notify_peeked_chat(message, target_chat_id, message.chat.title or "Неизвестный чат", bot)
    await message.reply("\n".join(recent_messages))

# ✅ Функция загрузки списка чатов с отключёнными смс
def load_sms_disabled_chats():
    """
    Загружает чаты с отключенными СМС из файла.
    Модифицирует глобальное множество `sms_disabled_chats` на месте, чтобы все модули видели изменения.
    """
    if os.path.exists(SMS_DISABLED_CHATS_FILE):
        try:
            with open(SMS_DISABLED_CHATS_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                # Проверяем, что из файла загрузился именно список
                if isinstance(data, list):
                    # ИСПРАВЛЕНИЕ: Очищаем и обновляем существующий объект, а не создаем новый
                    sms_disabled_chats.clear()
                    sms_disabled_chats.update(data)
                    logging.info(f"Загружено {len(sms_disabled_chats)} чатов с отключёнными смс.")
                else:
                    sms_disabled_chats.clear()
                    logging.warning(f"Файл {SMS_DISABLED_CHATS_FILE} содержит не список, а {type(data)}. Настройки сброшены.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке списка отключённых смс: {e}")
            sms_disabled_chats.clear()
    else:
        # Если файла нет, просто убедимся, что множество пустое
        sms_disabled_chats.clear()

# ✅ Функция сохранения списка чатов с отключёнными смс
def save_sms_disabled_chats():
    try:
        with open(SMS_DISABLED_CHATS_FILE, "w", encoding="utf-8") as file:
            # Преобразуем множество в список для сохранения в JSON
            json.dump(list(sms_disabled_chats), file, ensure_ascii=False, indent=4)
        logging.info("Список чатов с отключёнными смс сохранён.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении списка отключённых смс: {e}")

# ✅ Загружаем список отключённых чатов при старте бота
load_sms_disabled_chats()

# Вынесенная логика отключения СМС
async def process_disable_sms(chat_id, user_id, bot):
    # Проверяем, является ли пользователь админом или суперюзером
    chat_member = await bot.get_chat_member(chat_id, user_id)
    is_admin = chat_member.status in ["administrator", "creator"]
    is_superuser = user_id == 126386976  # 👑 Наш суперюзер
    
    if not (is_admin or is_superuser):
        return "Ты не админ и не бог, иди нахуй."
    
    chat_id_str = str(chat_id)
    if chat_id_str in sms_disabled_chats:
        return "СМС и ММС уже отключены в этом чате."
    else:
        sms_disabled_chats.add(chat_id_str)
        save_sms_disabled_chats()
        return "Теперь я не принимаю и не отправляю смс и ммс в этом чате."

# Вынесенная логика включения СМС
async def process_enable_sms(chat_id, user_id, bot):
    # Проверяем, является ли пользователь админом
    chat_member = await bot.get_chat_member(chat_id, user_id)
    if chat_member.status not in ["administrator", "creator"]:
        return "Ты не админ, иди нахуй."
    
    chat_id_str = str(chat_id)
    if chat_id_str in sms_disabled_chats:
        sms_disabled_chats.remove(chat_id_str)
        save_sms_disabled_chats()
        return "Теперь я снова принимаю и отправляю смс и ммс в этом чате."
    else:
        return "СМС и ММС уже разрешены в этом чате."

# 🔴 ИСПРАВЛЕННАЯ ЛОГИКА ОТПРАВКИ СМС
async def process_send_sms(message: types.Message, chat_list: list, bot: Bot):
    chat_id = str(message.chat.id)
    is_reply = message.reply_to_message is not None
    
    # ИСПРАВЛЕНИЕ 1: Читаем команду из .text или .caption, как в ММС
    command_text = message.text or message.caption
    if not command_text:
        # На случай, если хэндлер сработал на сообщение без текста или caption
        logging.warning("process_send_sms вызван без command_text")
        return
        
    parts = command_text.split(maxsplit=2)  # Разделяем команду
    
    text_message = None # Текст для отправки

    # ИСПРАВЛЕНИЕ 2: Новая логика определения текста
    # Сначала ищем текст в самой команде (parts[2])
    if len(parts) > 2:
        text_message = parts[2]
    # Если текста в команде нет, И это реплай, берем текст из реплая
    elif is_reply:
        text_message = message.reply_to_message.text or message.reply_to_message.caption or "(без текста)"
    
    # Если текста все еще нет (т.е. не реплай И нет parts[2])
    if text_message is None:
        # Проверяем, был ли указан хотя бы номер чата
        if len(parts) < 2:
            await message.reply("эээ далбаеб: смс <номер чата> <текст> (или ответь на сообщение)")
            return
        else:
            # Случай "смс <номер>" без реплая и без текста
            text_message = "(без текста)"
            
    # --- Теперь остальная логика ---
    
    try:
        # Номер чата теперь всегда в parts[1]
        if len(parts) < 2:
            # Этот случай должен был отсечься выше, но для надежности
            await message.reply("эээ далбаеб: смс <номер чата> <текст>")
            return
            
        chat_index = int(parts[1]) - 1
        
        filtered_chats = _get_numbered_chats(chat_list)
        
        if chat_index < 0 or chat_index >= len(filtered_chats):
            await message.reply("Чат с таким номером не найден, иди нахуй")
            return
            
        target_chat_id = str(filtered_chats[chat_index]["id"])
        # Проверяем, отключены ли СМС в целевом чате
        if target_chat_id in sms_disabled_chats:
            await message.reply("Это хуесосы-бирюки, не принимают СМС, блядь")
            return
            
        source_chat_title = message.chat.title or "Неизвестный чат"
        # Находим номер исходного чата в отсортированном списке
        source_chat_number = next((i + 1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id), "❓")
        
        # Старая логика с "if is_reply:" больше не нужна,
        # так как text_message уже определен выше
        
        formatted_message = f'Вам песьмо из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{text_message}'
        await bot.send_message(target_chat_id, formatted_message)
        await message.reply(f"Песьмо отправлено в чат {filtered_chats[chat_index]['title']}!")
        
    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: смс <номер чата> <текст>")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в чат: {e}")
        await message.reply("Не удалось отправить сообщение. Возможно, я хуисос")

# Вынесенная логика отправки ММС (без изменений, как в вашем файле)
async def process_send_mms(message: types.Message, chat_list_param: list, bot: Bot):
    chat_list = chat_list_param  # Используем локальную переменную вместо global
    chat_id = str(message.chat.id)

    is_reply = message.reply_to_message is not None

    command_text = message.text or message.caption  
    parts = command_text.split(maxsplit=2)  

    if len(parts) < 2 and not is_reply:
        await message.reply("эээ далбаеб: ммс <номер чата> (и прикрепи медиафайл)")
        return

    try:
        chat_index = int(parts[1]) - 1
        
        filtered_chats = _get_numbered_chats(chat_list)
        
        if chat_index < 0 or chat_index >= len(filtered_chats):
            await message.reply("Чат с таким номером не найден, иди нахуй")
            return

        target_chat_id = str(filtered_chats[chat_index]["id"])

        # Проверяем, отключены ли ММС в целевом чате
        if target_chat_id in sms_disabled_chats:
            await message.reply("Это хуесосы-бирюки, не принимают ММС, блядь")
            return

        source_chat_title = message.chat.title or "Неизвестный чат"
        # Находим номер исходного чата в отсортированном списке
        source_chat_number = next((i + 1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id), "❓")
        user_text = parts[2] if len(parts) > 2 else ""  
        caption = f'Вам аткрытка из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{user_text}'

        media = None

        message_to_forward = message.reply_to_message if is_reply else message

        if message_to_forward.photo:
            media = message_to_forward.photo[-1].file_id
            await bot.send_photo(target_chat_id, media, caption=caption)
        elif message_to_forward.video:
            media = message_to_forward.video.file_id
            await bot.send_video(target_chat_id, media, caption=caption)
        elif message_to_forward.animation:
            media = message_to_forward.animation.file_id
            await bot.send_animation(target_chat_id, media, caption=caption)
        elif message_to_forward.audio:
            media = message_to_forward.audio.file_id
            await bot.send_audio(target_chat_id, media, caption=caption)
        elif message_to_forward.voice:
            media = message_to_forward.voice.file_id
            await bot.send_voice(target_chat_id, media, caption=caption)
        elif message_to_forward.document:
            media = message_to_forward.document.file_id
            await bot.send_document(target_chat_id, media, caption=caption)
        elif message_to_forward.sticker:
            media = message_to_forward.sticker.file_id
            await bot.send_sticker(target_chat_id, media)
        elif message_to_forward.poll:
            poll = message_to_forward.poll
            await bot.send_poll(
                chat_id=target_chat_id,
                question=poll.question,
                options=[option.text for option in poll.options],
                type='quiz' if poll.type == 'quiz' else 'regular',
                correct_option_id=poll.correct_option_id if poll.type == 'quiz' else None,
                explanation=poll.explanation,
                is_anonymous=poll.is_anonymous,
                allows_multiple_answers=poll.allows_multiple_answers
            )
            media = "poll" # To indicate success

        if media:
            await message.reply(f"Аткрытка отправлена в чат {filtered_chats[chat_index]['title']}!")
        else:
            await message.reply("Ошибка блядь: не удалось найти медиа для отправки.")

    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: ммс <номер чата> (и прикрепи медиафайл)")
    except Exception as e:
        logging.error(f"Ошибка при отправке аткрытки в чат: {e}")
        await message.reply("Не удалось отправить медиа. Возможно, я хуисос")

