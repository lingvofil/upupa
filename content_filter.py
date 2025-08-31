import json
import os
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
    "халтура", "оплата каждый день", "нужны деньги", "в лс", 
    "sanya_rf_work", "требуются", "заработок", "работа на дому"
]
REPETITION_LIMIT = {
    "max_repetitions": 3,
    "time_window": 60 
}
# --- КОНЕЦ НАСТРОЕК ---

# --- УПРАВЛЕНИЕ СОСТОЯНИЕМ ФИЛЬТРА (ИЗМЕНЕНО) ---
# Имя файла изменено, чтобы отразить новую логику
ANTISPAM_SETTINGS_FILE = "antispam_enabled.json"
# Теперь здесь хранятся ID чатов, где фильтр ВКЛЮЧЕН
ANTISPAM_ENABLED_CHATS = set() 

def load_antispam_settings():
    """Загружает ID чатов с ВКЛЮЧЕННЫМ антиспамом из файла."""
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
    """Сохраняет ID чатов с ВКЛЮЧЕННЫМ антиспамом в файл."""
    with open(ANTISPAM_SETTINGS_FILE, "w") as f:
        json.dump(list(ANTISPAM_ENABLED_CHATS), f)
# --- КОНЕЦ БЛОКА УПРАВЛЕНИЯ ---

user_recent_messages = defaultdict(list)
NORMALIZATION_TABLE = str.maketrans("aAeEoOpPcCxX", "аАеЕоОрРсСхХ")

def normalize_text(text: str) -> str:
    return text.lower().translate(NORMALIZATION_TABLE)

class ContentFilterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        
        # --- ЛОГИКА ИНВЕРТИРОВАНА ---
        # Теперь фильтр срабатывает, только если ID чата есть в списке ВКЛЮЧЕННЫХ.
        # Если его нет в списке, просто пропускаем сообщение дальше.
        if event.chat.id not in ANTISPAM_ENABLED_CHATS:
            return await handler(event, data)
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---

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

        # Проверка по стоп-словам
        for stop_word in STOP_WORDS:
            if stop_word in normalized_text:
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
                await event.answer(
                    f"Пользователь @{event.from_user.username} ({event.from_user.full_name}) "
                    f"получает временное ограничение на {MUTE_DURATION_SECONDS} секунд. Причина: {reason}."
                )
            except Exception as e:
                print(f"Не удалось обработать спам от {user_id} в чате {chat_id}: {e}")
            return

        return await handler(event, data)

