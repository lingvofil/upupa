import logging
import os
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.enums import ContentType
from aiogram.types import Message


_LOG_MESSAGE_CONTENT = os.getenv("LOG_MESSAGE_CONTENT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_LOG_MESSAGE_CONTENT_LIMIT = 200


def _message_preview(message_text: str | None) -> str | None:
    if not _LOG_MESSAGE_CONTENT or not message_text:
        return None
    return message_text.replace("\n", "\\n")[:_LOG_MESSAGE_CONTENT_LIMIT]


class IncomingMessageLogMiddleware(BaseMiddleware):
    """Log safe incoming-message metadata before handler processing.

    Message bodies are intentionally excluded from INFO logs by default. A
    short preview can be enabled explicitly with ``LOG_MESSAGE_CONTENT=true``
    for temporary diagnostics.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            message_text = event.text or event.caption
            preview = _message_preview(message_text)
            logging.info(
                "Входящее сообщение: чат=%s, пользователь_id=%s, тип=%s, "
                "длина_текста=%s%s",
                event.chat.id,
                event.from_user.id,
                event.content_type,
                len(message_text) if message_text else 0,
                f", текст='{preview}'" if preview is not None else "",
            )
            if event.content_type == ContentType.UNKNOWN:
                logging.info(
                    "UNKNOWN message metadata: chat=%s user_id=%s message_id=%s",
                    event.chat.id,
                    event.from_user.id,
                    event.message_id,
                )

        return await handler(event, data)
