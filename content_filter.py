import json
import os
import re
import asyncio
from typing import Callable, Dict, Any, Awaitable
from collections import defaultdict
from datetime import timedelta

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, ChatPermissions

from config import ADMIN_ID

# --- НАСТРОЙКИ ФИЛЬТРА ---
MUTE_DURATION_SECONDS = 60
STOP_WORDS = [
    "халтура", "оплата каждый день", "оплата по факту", "нужны деньги", "подробности в лс",
    "sanya_rf_work", "требуются", "заработок", "подработка", "подработку", "доход", "работа на дому",
    "нужна работа", "в лс", "в личные сообщения", "в личные сообщение", "в личку", "расскажу подробно"
]
REPETITION_LIMIT = {
    "max_repetitions": 3,
    "time_window": 60
}
# --- КОНЕЦ НАСТРОЕК ---

ANTISPAM_SETTINGS_FILE = "antispam_enabled.json"
ANTISPAM_ENABLED_CHATS = set()

def load_antispam_settings():
    if os.path.exists(ANTISPAM_SETTINGS_FILE):
        try:
            with open(ANTISPAM_SETTINGS_FILE, "r") as f:
                ANTISPAM_ENABLED_CHATS.update(json.load(f))
            print(f"Загружены настройки антиспама. Фильтр включен в {len(ANTISPAM_ENABLED_CHATS)} чатах.")
        except json.JSONDecodeError:
            print(f"Ошибка чтения файла {ANTISPAM_SETTINGS_FILE}. Файл может быть поврежден.")
    else:
        print("Файл настроек антиспама не найден. Фильтр отключен везде по умолчанию.")

def save_antispam_settings():
    with open(ANTISPAM_SETTINGS_FILE, "w") as f:
        json.dump(list(ANTISPAM_ENABLED_CHATS), f)

user_recent_messages = defaultdict(list)

# Расширенная таблица нормализации: латиница, греческие буквы, похожие символы
NORMALIZATION_TABLE = str.maketrans({
    # латиница на кириллицу
    "a": "а", "A": "А",
    "e": "е", "E": "Е",
    "o": "о", "O": "О",
    "p": "р", "P": "Р",
    "c": "с", "C": "С",
    "x": "х", "X": "Х",
    # греческие варианты
    "ο": "о",  # греческая омикрон
    "р": "р",  # иногда копируется, оставим для ясности
    "с": "с",
})

def normalize_text(text: str) -> str:
    # Убираем невидимые символы и лишние пробелы, приводим к нижнему регистру с casefold
    cleaned = re.sub(r"[\u200B-\u200D\uFEFF]", "", text)  # zero-width chars
    cleaned = re.sub(r"\s+", " ", cleaned)                # схлопываем пробелы
    return cleaned.casefold().translate(NORMALIZATION_TABLE)

class ContentFilterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:

        if event.chat.id not in ANTISPAM_ENABLED_CHATS:
            return await handler(event, data)

        if not (event.text or event.caption):
            return await handler(event, data)

        if event.from_user.id == ADMIN_ID:
            return await handler(event, data)

        bot: Bot = data['bot']
        user_id = event.from_user.id
        chat_id = event.chat.id
        now = event.date
        text = event.text or event.caption
        normalized_text = normalize_text(text)

        reason = ""

        # Проверка по стоп-словам (регулярка с гибкими пробелами)
        for stop_word in STOP_WORDS:
            pattern = re.sub(r"\\s+", r"\\s+", stop_word)  # разрешаем любые пробелы
            if re.search(pattern, normalized_text):
                reason = "обнаружение спама в сообщении"
                break

        # Проверка на повторения
        if not reason:
            time_window = timedelta(seconds=REPETITION_LIMIT['time_window'])
            user_recent_messages[user_id] = [
                (ts, msg) for ts, msg in user_recent_messages[user_id] if now - ts < time_window
            ]
            user_recent_messages[user_id].append((now, normalized_text))
            repetitions = sum(1 for _, msg in user_recent_messages[user_id] if msg == normalized_text)

            if repetitions >= REPETITION_LIMIT['max_repetitions']:
                reason = "повторяющиеся сообщения (флуд)"

        if reason:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=event.message_id)
                mute_duration = timedelta(seconds=MUTE_DURATION_SECONDS)
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=now + mute_duration
                )
            except Exception as e:
                print(f"Не удалось обработать спам от {user_id} в чате {chat_id}: {e}")
            return

        return await handler(event, data)
