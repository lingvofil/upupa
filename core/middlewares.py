import json
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.enums import ContentType
from aiogram.types import Message


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
            if event.content_type == ContentType.UNKNOWN:
                raw_event = event.model_dump(exclude_none=True)
                logging.info(
                    "UNKNOWN message payload: %s",
                    json.dumps(raw_event, ensure_ascii=False, default=str)[:4000],
                )

        return await handler(event, data)
