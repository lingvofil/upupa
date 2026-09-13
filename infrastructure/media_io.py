"""Безопасные асинхронные загрузчики бинарных медиа.

Модуль держит сетевые таймауты и ограничения размера в одном месте и не
формирует Telegram file URL с токеном вручную.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Mapping

import httpx


DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_MEDIA_BYTES = 50 * 1024 * 1024


class MediaDownloadTooLarge(ValueError):
    """Загружаемый файл превышает разрешённый размер."""


def _validate_size(size: int, max_bytes: int) -> None:
    if size > max_bytes:
        raise MediaDownloadTooLarge(
            f"Медиафайл превышает лимит {max_bytes} байт: {size} байт"
        )


async def download_telegram_bytes(
    bot,
    file_id: str,
    *,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> bytes:
    """Скачать Telegram-файл через Bot API без ручной сборки URL с токеном."""
    file_info = await asyncio.wait_for(bot.get_file(file_id), timeout=timeout)

    declared_size = getattr(file_info, "file_size", None)
    if declared_size is not None:
        _validate_size(int(declared_size), max_bytes)

    destination = BytesIO()
    await asyncio.wait_for(
        bot.download_file(file_info.file_path, destination=destination),
        timeout=timeout,
    )
    data = destination.getvalue()
    _validate_size(len(data), max_bytes)
    return data


async def download_url_bytes(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Асинхронно скачать URL потоково с таймаутом и лимитом размера."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
    )

    try:
        async with http_client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    _validate_size(int(content_length), max_bytes)
                except ValueError:
                    # Некорректный Content-Length не должен ломать загрузку:
                    # фактический размер всё равно контролируется по чанкам.
                    pass

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                _validate_size(total, max_bytes)
                chunks.append(chunk)

            return b"".join(chunks)
    finally:
        if owns_client:
            await http_client.aclose()
