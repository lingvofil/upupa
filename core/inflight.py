"""Не больше одного одновременного запуска тяжёлой команды на чат или пользователя.

Генерации картинок, видео и радио длятся минутами и занимают общие AI-слоты.
Повторный запрос, пока первый ещё работает, только удлиняет очередь для всех.
"""

from __future__ import annotations

from functools import wraps
from typing import Literal


_active: set[tuple] = set()


def _key(feature: str, scope: str, message) -> tuple | None:
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if scope == "chat":
        return (feature, chat_id)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is None:
        return None
    return (feature, chat_id, user_id)


def single_flight(feature: str, *, per: Literal["chat", "user"], busy_text: str):
    """Декоратор message-хэндлера: пока запуск идёт, повторный получает busy_text."""

    def decorator(handler):
        @wraps(handler)
        async def wrapper(message, *args, **kwargs):
            key = _key(feature, per, message)
            if key is None:
                return await handler(message, *args, **kwargs)
            if key in _active:
                await message.reply(busy_text)
                return None
            _active.add(key)
            try:
                return await handler(message, *args, **kwargs)
            finally:
                _active.discard(key)

        return wrapper

    return decorator


__all__ = ["single_flight"]
