"""Чтение свежих публичных постов из Telegram-канала «бати» через web-preview.

Bot API не позволяет получать произвольную историю чужого канала. Для публичного
канала используем HTML-ленту https://t.me/s/<username>. Ошибки чтения не должны
ломать основной автопостинг: вызывающий код просто вернётся к обычному посту.
"""

from __future__ import annotations

import logging
import os

import aiohttp
from bs4 import BeautifulSoup

BATYA_CHANNEL = os.getenv("UPUPA_BATYA_CHANNEL", "lukeimyourmouth").lstrip("@")
REQUEST_TIMEOUT_SECONDS = 12
USER_AGENT = "Mozilla/5.0 (compatible; UpupaBot/1.0; +https://t.me/upupa_channel)"


def _parse_public_feed(html: str, *, channel: str = BATYA_CHANNEL) -> list[dict]:
    """Парсит Telegram public preview и возвращает посты от старых к новым."""
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict] = []
    prefix = f"{channel}/"

    for node in soup.select(".tgme_widget_message[data-post]"):
        data_post = str(node.get("data-post") or "").strip()
        if not data_post.startswith(prefix):
            continue

        message_id = data_post[len(prefix):]
        if not message_id.isdigit():
            continue

        text_node = node.select_one(".tgme_widget_message_text")
        text = text_node.get_text("\n", strip=True) if text_node else ""
        if not text:
            # Пока не притворяемся, что Упупа умеет видеть фото/видео из web-preview.
            continue

        posts.append(
            {
                "message_id": int(message_id),
                "url": f"https://t.me/{channel}/{message_id}",
                "text": text,
            }
        )

    posts.sort(key=lambda item: item["message_id"])
    return posts


async def fetch_batya_posts(*, limit: int = 20) -> list[dict]:
    """Получает последние текстовые посты публичного канала; при ошибке возвращает []."""
    url = f"https://t.me/s/{BATYA_CHANNEL}"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    logging.warning("[channel] batya feed HTTP %s", response.status)
                    return []
                html = await response.text()
    except Exception as exc:
        logging.warning("[channel] batya feed unavailable: %s", exc)
        return []

    try:
        posts = _parse_public_feed(html)
    except Exception as exc:
        logging.warning("[channel] batya feed parse failed: %s", exc)
        return []

    return posts[-limit:] if limit > 0 else posts
