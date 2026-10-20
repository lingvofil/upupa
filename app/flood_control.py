"""Повтор запросов Telegram после flood control (HTTP 429 / RetryAfter).

Ответ 429 означает, что Telegram запрос не выполнил, поэтому повтор не создаёт
дублей. Без повтора сообщение просто теряется: aiogram сам не ждёт retry_after.
"""

import asyncio
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetUpdates


DEFAULT_MAX_RETRY_WAIT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2


class RetryAfterMiddleware(BaseRequestMiddleware):
    def __init__(
        self,
        *,
        max_wait_seconds: float = DEFAULT_MAX_RETRY_WAIT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep=asyncio.sleep,
    ) -> None:
        self.max_wait_seconds = max_wait_seconds
        self.max_retries = max_retries
        self._sleep = sleep

    async def __call__(self, make_request, bot, method):
        attempt = 0
        while True:
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                # Polling восстанавливается сам; долгие паузы не держат обработчик.
                if (
                    isinstance(method, GetUpdates)
                    or attempt >= self.max_retries
                    or exc.retry_after > self.max_wait_seconds
                ):
                    raise
                attempt += 1
                logging.warning(
                    "Telegram flood control method=%s chat=%s retry=%d/%d after=%ss",
                    type(method).__name__,
                    getattr(method, "chat_id", None),
                    attempt,
                    self.max_retries,
                    exc.retry_after,
                )
                await self._sleep(exc.retry_after)


__all__ = ["RetryAfterMiddleware"]
