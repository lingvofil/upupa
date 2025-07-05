# middlewares.py
import asyncio
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

from statistics import log_message # Импортируем вашу функцию логирования

class StatisticsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        """
        Этот middleware будет перехватывать все сообщения,
        логировать их и передавать дальше.
        """
        # Мы хотим обрабатывать только сообщения (Message), а не другие события.
        if not isinstance(event, Message):
            return await handler(event, data)
        
        # Убедимся, что у сообщения есть пользователь (для служебных сообщений)
        if not event.from_user:
            return await handler(event, data)

        try:
            # Запускаем логирование в фоне, чтобы не замедлять бота
            is_private = event.chat.type == 'private'
            content_type = event.content_type
            
            asyncio.create_task(
                log_message(
                    chat_id=event.chat.id,
                    user_id=event.from_user.id,
                    message_type=content_type,
                    is_private=is_private
                )
            )
        except Exception as e:
            # Если при логировании возникнет ошибка, мы ее увидим в консоли,
            # но бот не упадет и продолжит работать.
            print(f"Error in StatisticsMiddleware: {e}")

        # Самое главное: передаем управление следующему хэндлеру в цепочке.
        return await handler(event, data)
