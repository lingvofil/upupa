"""Безопасная legacy-Markdown разметка для пользовательских строк.

Имена участников и ответы моделей могут содержать ``_``, ``*``, ``[`` или
обратные кавычки. В ``parse_mode="Markdown"`` такой текст Telegram отклоняет
целиком («can't parse entities»), и сообщение теряется.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest


_LEGACY_MARKDOWN_SPECIAL_RE = re.compile(r"([_*`\[])")
_LEGACY_MARKDOWN_MARKUP_RE = re.compile(r"\\([_*`\[])|[*`]")


def escape_markdown(value: Any) -> str:
    """Экранировать подставляемую строку для ``parse_mode="Markdown"``."""
    return _LEGACY_MARKDOWN_SPECIAL_RE.sub(r"\\\1", str(value or ""))


def markdown_to_plain(text: str) -> str:
    """Убрать legacy-Markdown разметку, сохранив экранированные символы."""
    return _LEGACY_MARKDOWN_MARKUP_RE.sub(lambda match: match.group(1) or "", text)


def is_entity_parse_error(exc: BaseException) -> bool:
    return isinstance(exc, TelegramBadRequest) and "can't parse entities" in str(exc).lower()


async def send_markdown_or_plain(
    send: Callable[..., Awaitable[Any]],
    text: str,
    /,
    *,
    text_field: str | None = None,
    **kwargs: Any,
) -> Any:
    """Отправить с Markdown, а если Telegram не разобрал разметку — простым текстом.

    ``send`` получает текст первым позиционным аргументом (``bot.send_message``
    через ``partial`` с chat_id, ``message.answer``) или, если задан
    ``text_field``, именованным аргументом (``caption`` у фото).
    """
    def call(value: str, parse_mode: str | None):
        if text_field is None:
            return send(value, parse_mode=parse_mode, **kwargs)
        return send(parse_mode=parse_mode, **{text_field: value}, **kwargs)

    try:
        return await call(text, "Markdown")
    except TelegramBadRequest as exc:
        if not is_entity_parse_error(exc):
            raise
        logging.warning("Markdown rejected by Telegram, resending as plain text: %s", exc)
    return await call(markdown_to_plain(text), None)


__all__ = [
    "send_markdown_or_plain",
    "escape_markdown",
    "is_entity_parse_error",
    "markdown_to_plain",
]
