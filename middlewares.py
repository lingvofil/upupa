# middlewares.py
import asyncio
import logging
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


class IncomingMessageLogMiddleware(BaseMiddleware):
    """Логирует все входящие сообщения до их обработки хэндлерами."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            message_text = event.text or event.caption
            safe_text = "<без текста>"
            if message_text:
                safe_text = message_text.replace("\n", "\\n")

            logging.info(
                "Входящее сообщение: чат=%s, пользователь=%s, тип=%s, текст='%s'",
                event.chat.id,
                event.from_user.full_name,
                event.content_type,
                safe_text,
            )

        return await handler(event, data)
