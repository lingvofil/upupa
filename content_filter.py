import asyncio
from typing import Callable, Dict, Any, Awaitable
from collections import defaultdict
from datetime import timedelta

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, ChatPermissions

# Импортируем только ADMIN_ID из глобального конфига, чтобы его не трогать
from config import ADMIN_ID

# --- НАСТРОЙКИ ФИЛЬТРА ---

# Длительность мута в секундах
MUTE_DURATION_SECONDS = 60

# Список стоп-слов. Фильтр не чувствителен к регистру и замене букв.
# Вы можете легко пополнять этот список.
STOP_WORDS = [
    "халтура", "оплата каждый день", "нужны деньги", "подробности в лс", 
    "sanya_rf_work", "требуются", "заработок", "работа на дому"
]

# Лимит на повторения: не более 3 одинаковых сообщений за 60 секунд
REPETITION_LIMIT = {
    "max_repetitions": 2,
    "time_window": 60 
}
# --- КОНЕЦ НАСТРОЕК ---


# Словарь для отслеживания повторяющихся сообщений
# Структура: {user_id: [(timestamp, message_text), ...]}
user_recent_messages = defaultdict(list)

# Таблица для нормализации текста (обход защиты заменой букв)
NORMALIZATION_TABLE = str.maketrans(
    "aAeEoOpPcCxX",  # Латинские буквы
    "аАеЕоОрРсСхХ"   # Похожие на них кириллические
)

def normalize_text(text: str) -> str:
    """Приводит текст к нижнему регистру и заменяет похожие латинские буквы на кириллические."""
    return text.lower().translate(NORMALIZATION_TABLE)

class ContentFilterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        
        # Фильтруем только текстовые сообщения и сообщения с подписями
        if not (event.text or event.caption):
            return await handler(event, data)
        
        # Администратора не трогаем
        if event.from_user.id == ADMIN_ID:
            return await handler(event, data)

        bot: Bot = data['bot']
        user_id = event.from_user.id
        chat_id = event.chat.id
        now = event.date
        text = event.text or event.caption
        normalized_text = normalize_text(text)
        
        reason = ""

        # 1. Проверка по стоп-словам
        for stop_word in STOP_WORDS:
            if stop_word in normalized_text:
                reason = "обнаружение спама в сообщении"
                break
        
        # 2. Проверка на повторения, если не нашли спам по словам
        if not reason:
            time_window = timedelta(seconds=REPETITION_LIMIT['time_window'])
            
            # Очищаем старые сообщения
            user_recent_messages[user_id] = [
                (ts, msg) for ts, msg in user_recent_messages[user_id] if now - ts < time_window
            ]
            
            # Добавляем текущее сообщение
            user_recent_messages[user_id].append((now, normalized_text))
            
            # Считаем повторы
            repetitions = sum(1 for _, msg in user_recent_messages[user_id] if msg == normalized_text)
            
            if repetitions >= REPETITION_LIMIT['max_repetitions']:
                reason = "повторяющиеся сообщения (флуд)"
        
        # Если нашли спам (по любой из причин)
        if reason:
            try:
                # Сначала удаляем спам-сообщение
                await bot.delete_message(chat_id=chat_id, message_id=event.message_id)

                # Затем выдаем мут
                mute_duration = timedelta(seconds=MUTE_DURATION_SECONDS)
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=now + mute_duration
                )
                
                # Отправляем уведомление (оно не будет удалено)
                await event.answer(
                    f"Пользователь @{event.from_user.username} ({event.from_user.full_name}) "
                    f"получает временное ограничение на {MUTE_DURATION_SECONDS} секунд. Причина: {reason}."
                )

            except Exception as e:
                # В лог можно добавить, если у бота нет прав в чате
                print(f"Не удалось обработать спам от {user_id} в чате {chat_id}: {e}")
            
            # Прерываем обработку сообщения
            return

        return await handler(event, data)
