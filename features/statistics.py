import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Protocol
import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

from core.settings import ADMIN_ID

# --- Rate limit для ЛС ---
private_message_timestamps: Dict[int, datetime] = {}
PRIVATE_MESSAGE_COOLDOWN = timedelta(hours=1)


class StatisticsRepository(Protocol):
    """Persistence contract used by the statistics feature."""

    def init_schema(self) -> None: ...

    def log_model_request(
        self,
        chat_id: int | None,
        user_id: int | None,
        model_name: str,
        request_type: str,
    ) -> None: ...

    def log_message(
        self,
        chat_id: int,
        user_id: int,
        message_type: str,
        is_private: bool,
        chat_title: str | None,
        user_name: str,
        user_username: str | None,
    ) -> None: ...

    def get_stats(self, period_hours: int | None = None) -> dict[str, dict]: ...

    def get_activity_by_hour(self, period_hours: int | None = None) -> dict[int, int]: ...

    def get_group_chat_activity(
        self,
        active_since: datetime,
    ) -> dict[int, datetime]: ...


_statistics_repository: StatisticsRepository | None = None


def configure_statistics_repository(repository: StatisticsRepository) -> None:
    """Wire the concrete persistence adapter from the application composition root."""
    global _statistics_repository
    _statistics_repository = repository


def _repository() -> StatisticsRepository:
    if _statistics_repository is None:
        raise RuntimeError("Statistics repository is not configured")
    return _statistics_repository


class PrivateRateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if event.chat.type == 'private' and event.from_user:
            user_id = event.from_user.id
            if user_id != ADMIN_ID:
                now = datetime.now()
                last = private_message_timestamps.get(user_id)
                if last and now - last < PRIVATE_MESSAGE_COOLDOWN:
                    await event.reply("иди общайся в чат, хитрый педераст")
                    return
                private_message_timestamps[user_id] = now
        return await handler(event, data)


# --- Инициализация БД ---

def init_db():
    _repository().init_schema()


# --- Логирование использования нейросетей ---

def log_model_request(chat_id: Optional[int], user_id: Optional[int], model_name: str, request_type: str):
    try:
        _repository().log_model_request(chat_id, user_id, model_name, request_type)
    except Exception as e:
        logging.error(f"Error logging model request: {e}")


# --- Логирование сообщений ---

async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool,
                      chat_title: Optional[str], user_name: str, user_username: Optional[str]):
    try:
        await asyncio.to_thread(
            _repository().log_message,
            chat_id,
            user_id,
            message_type,
            is_private,
            chat_title,
            user_name,
            user_username,
        )
    except Exception as e:
        logging.error(f"Error logging message: {e}")


# --- Получение статистики ---

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    return _repository().get_stats(period_hours)


async def get_total_messages():
    return await asyncio.to_thread(_repository().get_stats, None)


async def get_messages_last_24_hours():
    return await asyncio.to_thread(_repository().get_stats, 24)


async def get_messages_last_hour():
    return await asyncio.to_thread(_repository().get_stats, 1)


async def get_activity_by_hour(period_hours: Optional[int] = None) -> Dict[int, int]:
    return await asyncio.to_thread(_repository().get_activity_by_hour, period_hours)



async def get_group_chat_activity(
    active_since: datetime,
) -> dict[int, datetime]:
    return await asyncio.to_thread(
        _repository().get_group_chat_activity,
        active_since,
    )
