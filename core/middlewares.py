import logging
import os
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.enums import ContentType
from aiogram.types import Message

from infrastructure.ai.execution import ai_request_context


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


class AIUsageContextMiddleware(BaseMiddleware):
    """Attach Telegram chat/user metadata to every nested AI provider call."""

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        message = event if isinstance(event, Message) else None
        user = getattr(event, "from_user", None)

        if message is None:
            message = (
                getattr(event, "message", None)
                or getattr(event, "edited_message", None)
                or getattr(event, "channel_post", None)
                or getattr(event, "edited_channel_post", None)
            )

        callback = getattr(event, "callback_query", None)
        if callback is not None:
            user = getattr(callback, "from_user", None) or user
            message = getattr(callback, "message", None) or message

        if user is None and message is not None:
            user = getattr(message, "from_user", None)

        chat = getattr(message, "chat", None) if message is not None else None
        chat_id = getattr(chat, "id", None)
        user_id = getattr(user, "id", None)

        with ai_request_context(
            chat_id=int(chat_id) if chat_id is not None else None,
            user_id=int(user_id) if user_id is not None else None,
            chat_title=getattr(chat, "title", None) if chat is not None else None,
            user_name=getattr(user, "full_name", None) if user is not None else None,
            user_username=getattr(user, "username", None) if user is not None else None,
        ):
            return await handler(event, data)
