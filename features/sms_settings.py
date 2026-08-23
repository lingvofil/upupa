import asyncio
import os
import logging
import re
from collections import deque
from datetime import datetime, timedelta
from aiogram import Bot, types

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import SMS_DISABLED_CHATS_PATH, USER_MESSAGES_LOG_PATH
from core.settings import SPECIAL_CHAT_ID
from core.state import sms_disabled_chats

LOG_FILE = str(USER_MESSAGES_LOG_PATH)

LOG_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_REPLY_SAFE_LIMIT = TELEGRAM_MESSAGE_LIMIT - 96

USER_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^\n]*?)"
    r"\s+-\s+Chat\s+(?P<chat_id>-?\d+)\b"
    r"(?:\s+\(.*?\))?"
    r"\s+-\s+User(?:\s+\S+)?"
    r"(?:\s+\((?P<username>.*?)\))?"
    r"(?:\s+\[(?P<full_name>.*?)\])?"
    r":\s*(?P<text>.*)\Z",
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
        if LOG_START_RE.match(line):
            if current_record is not None:
                yield current_record
            current_record = line
        elif current_record is not None:
            current_record += line

    if current_record is not None:
        yield current_record


def _fit_recent_messages_to_telegram_limit(messages, max_length: int = TELEGRAM_REPLY_SAFE_LIMIT) -> list[str]:
    """Обрезает вывод под лимит Telegram, сохраняя самые последние сообщения."""
    fitted_messages = deque()
    current_length = 0

    for message in reversed(messages):
        separator_length = 1 if fitted_messages else 0
        available_length = max_length - current_length - separator_length
        if available_length <= 0:
            break

        if len(message) <= available_length:
            fitted_messages.appendleft(message)
            current_length += len(message) + separator_length
            continue

        if available_length > 1:
            fitted_messages.appendleft("…" + message[-(available_length - 1):])
        break

    return list(fitted_messages)


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

    if str(message.chat.id) in sms_disabled_chats:
        await message.reply("СМС/ММС отключены в этом чате, чоговорят тоже нельзя.")
        return

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

    if target_chat_id in sms_disabled_chats:
        await message.reply("В этом чате СМС/ММС отключены, подсматривать туда тоже нельзя.")
        return

    recent_messages = deque(maxlen=10)

    if not os.path.exists(LOG_FILE):
        await message.reply("Пока нечего рассказать: лог сообщений пуст.")
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as file:
            for record in _iter_user_log_records(file):
                parsed = _parse_user_log_line(record)
                if not parsed:
                    logging.warning(f"Не удалось распарсить запись лога:\n{record}")
                    continue
                if parsed["chat_id"] != target_chat_id:
                    continue

                text = parsed["text"].strip().replace("\n", " / ")
                if not text:
                    continue

                formatted_message = (
                    f"{_format_log_time(parsed['timestamp'])} "
                    f"{_format_log_author(parsed['username'], parsed['full_name'])}: {text}"
                )
                if recent_messages and recent_messages[-1] == formatted_message:
                    continue
                recent_messages.append(formatted_message)
    except Exception as e:
        logging.error(f"Ошибка при чтении последних сообщений чата {target_chat_id}: {e}")
        await message.reply("Не удалось прочитать сообщения. Возможно, я хуисос")
        return

    if not recent_messages:
        await message.reply(f"В чате {target_chat.get('title', target_chat_id)} пока нет сохранённых сообщений.")
        return

    await _notify_peeked_chat(message, target_chat_id, message.chat.title or "Неизвестный чат", bot)
    fitted_messages = _fit_recent_messages_to_telegram_limit(recent_messages)
    await message.reply("\n".join(fitted_messages))


def _sms_disabled_repository() -> JsonFileRepository:
    return JsonFileRepository(SMS_DISABLED_CHATS_PATH)


def load_sms_disabled_chats(repository: JsonRepository | None = None):
    """Загрузить список отключённых чатов, сохраняя identity shared set."""
    repo = repository or _sms_disabled_repository()
    try:
        data = repo.load()
    except FileNotFoundError:
        sms_disabled_chats.clear()
        return
    except Exception as e:
        logging.error(f"Ошибка при загрузке списка отключённых смс: {e}")
        sms_disabled_chats.clear()
        return

    if isinstance(data, list):
        sms_disabled_chats.clear()
        sms_disabled_chats.update(data)
        logging.info(f"Загружено {len(sms_disabled_chats)} чатов с отключёнными смс.")
    else:
        sms_disabled_chats.clear()
        logging.warning(
            f"Файл {SMS_DISABLED_CHATS_PATH} содержит не список, а {type(data)}. Настройки сброшены."
        )


def save_sms_disabled_chats(repository: JsonRepository | None = None):
    repo = repository or _sms_disabled_repository()
    try:
        repo.save(list(sms_disabled_chats))
        logging.info("Список чатов с отключёнными смс сохранён.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении списка отключённых смс: {e}")


async def process_disable_sms(chat_id, user_id, bot):
    chat_member = await bot.get_chat_member(chat_id, user_id)
    is_admin = chat_member.status in ["administrator", "creator"]
    is_superuser = user_id == 126386976

    if not (is_admin or is_superuser):
        return "Ты не админ и не бог, иди нахуй."

    chat_id_str = str(chat_id)
    if chat_id_str in sms_disabled_chats:
        return "СМС и ММС уже отключены в этом чате."

    sms_disabled_chats.add(chat_id_str)
    await asyncio.to_thread(save_sms_disabled_chats)
    return "Теперь я не принимаю и не отправляю смс и ммс в этом чате."


async def process_enable_sms(chat_id, user_id, bot):
    chat_member = await bot.get_chat_member(chat_id, user_id)
    if chat_member.status not in ["administrator", "creator"]:
        return "Ты не админ, иди нахуй."

    chat_id_str = str(chat_id)
    if chat_id_str in sms_disabled_chats:
        sms_disabled_chats.remove(chat_id_str)
        await asyncio.to_thread(save_sms_disabled_chats)
        return "Теперь я снова принимаю и отправляю смс и ммс в этом чате."
    return "СМС и ММС уже разрешены в этом чате."


async def process_send_sms(message: types.Message, chat_list: list, bot: Bot):
    chat_id = str(message.chat.id)
    is_reply = message.reply_to_message is not None

    command_text = message.text or message.caption
    if not command_text:
        logging.warning("process_send_sms вызван без command_text")
        return

    parts = command_text.split(maxsplit=2)

    text_message = None
    if len(parts) > 2:
        text_message = parts[2]
    elif is_reply:
        text_message = message.reply_to_message.text or message.reply_to_message.caption or "(без текста)"

    if text_message is None:
        if len(parts) < 2:
            await message.reply("эээ далбаеб: смс <номер чата> <текст> (или ответь на сообщение)")
            return
        text_message = "(без текста)"

    try:
        if len(parts) < 2:
            await message.reply("эээ далбаеб: смс <номер чата> <текст>")
            return

        chat_index = int(parts[1]) - 1
        filtered_chats = _get_numbered_chats(chat_list)

        if chat_index < 0 or chat_index >= len(filtered_chats):
            await message.reply("Чат с таким номером не найден, иди нахуй")
            return

        target_chat_id = str(filtered_chats[chat_index]["id"])
        if target_chat_id in sms_disabled_chats:
            await message.reply("Это хуесосы-бирюки, не принимают СМС, блядь")
            return

        source_chat_title = message.chat.title or "Неизвестный чат"
        source_chat_number = next(
            (i + 1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id),
            "❓",
        )

        formatted_message = f'Вам песьмо из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{text_message}'
        await bot.send_message(target_chat_id, formatted_message)
        await message.reply(f"Песьмо отправлено в чат {filtered_chats[chat_index]['title']}!")

    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: смс <номер чата> <текст>")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в чат: {e}")
        await message.reply("Не удалось отправить сообщение. Возможно, я хуисос")


async def process_send_mms(message: types.Message, chat_list_param: list, bot: Bot):
    chat_list = chat_list_param
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

        if target_chat_id in sms_disabled_chats:
            await message.reply("Это хуесосы-бирюки, не принимают ММС, блядь")
            return

        source_chat_title = message.chat.title or "Неизвестный чат"
        source_chat_number = next(
            (i + 1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id),
            "❓",
        )
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
        elif message_to_forward.video_note:
            media = message_to_forward.video_note.file_id
            await bot.send_video_note(target_chat_id, media)
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
            media = "poll"

        if media:
            await message.reply(f"Аткрытка отправлена в чат {filtered_chats[chat_index]['title']}!")
        else:
            await message.reply("Ошибка блядь: не удалось найти медиа для отправки.")

    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: ммс <номер чата> (и прикрепи медиафайл)")
    except Exception as e:
        logging.error(f"Ошибка при отправке аткрытки в чат: {e}")
        await message.reply("Не удалось отправить медиа. Возможно, я хуисос")
