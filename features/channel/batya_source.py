"""Чтение свежих публичных Telegram-постов через web-preview.

Bot API не позволяет получать произвольную историю чужих каналов. Для публичных
каналов используем HTML-ленту https://t.me/s/<username>. Ошибки чтения не должны
ломать основной автопостинг: вызывающий код просто вернётся к обычному посту.
"""

from __future__ import annotations

import logging
import os
import re

import aiohttp
from bs4 import BeautifulSoup

BATYA_CHANNEL = os.getenv("UPUPA_BATYA_CHANNEL", "lukeimyourmouth").lstrip("@")
REQUEST_TIMEOUT_SECONDS = 12
MAX_PUBLIC_IMAGE_BYTES = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; UpupaBot/1.0; +https://t.me/upupa_channel)"
_IMAGE_URL_RE = re.compile(r"url\((?P<quote>['\"]?)(?P<url>.*?)(?P=quote)\)", re.IGNORECASE)


def _extract_photo_url(node) -> str | None:
    photo_node = node.select_one(".tgme_widget_message_photo_wrap")
    if not photo_node:
        return None
    style = str(photo_node.get("style") or "")
    match = _IMAGE_URL_RE.search(style)
    if not match:
        return None
    url = match.group("url").strip()
    return url or None


def _parse_public_feed(html: str, *, channel: str = BATYA_CHANNEL) -> list[dict]:
    """Парсит Telegram public preview и возвращает текстовые/фото-посты от старых к новым."""
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
        text = " ".join(text_node.stripped_strings) if text_node else ""
        image_url = _extract_photo_url(node)
        if not text and not image_url:
            continue

        post = {
            "message_id": int(message_id),
            "url": f"https://t.me/{channel}/{message_id}",
            "text": text,
        }
        if image_url:
            post["image_url"] = image_url
        posts.append(post)

    posts.sort(key=lambda item: item["message_id"])
    return posts


async def fetch_public_posts(channel: str, *, limit: int = 20) -> list[dict]:
    """Получает последние текстовые/фото-посты публичного канала; при ошибке возвращает []."""
    channel = str(channel or "").strip().lstrip("@")
    if not channel:
        return []

    url = f"https://t.me/s/{channel}"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    logging.warning("[channel] public feed @%s HTTP %s", channel, response.status)
                    return []
                html = await response.text()
    except Exception as exc:
        logging.warning("[channel] public feed @%s unavailable: %s", channel, exc)
        return []

    try:
        posts = _parse_public_feed(html, channel=channel)
    except Exception as exc:
        logging.warning("[channel] public feed @%s parse failed: %s", channel, exc)
        return []

    return posts[-limit:] if limit > 0 else posts


async def fetch_public_image(image_url: str) -> tuple[bytes, str] | None:
    """Скачивает фото из Telegram web-preview для последующего vision-анализа."""
    image_url = str(image_url or "").strip()
    if not image_url.startswith(("https://", "http://")):
        return None

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(image_url, allow_redirects=True) as response:
                if response.status != 200:
                    logging.warning("[channel] public image HTTP %s", response.status)
                    return None
                if response.content_length and response.content_length > MAX_PUBLIC_IMAGE_BYTES:
                    logging.warning("[channel] public image too large: %s bytes", response.content_length)
                    return None
                data = await response.read()
                if not data or len(data) > MAX_PUBLIC_IMAGE_BYTES:
                    return None
                mime_type = str(response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip()
                if not mime_type.startswith("image/"):
                    mime_type = "image/jpeg"
                return data, mime_type
    except Exception as exc:
        logging.warning("[channel] public image unavailable: %s", exc)
        return None


async def fetch_batya_posts(*, limit: int = 20) -> list[dict]:
    """Совместимый wrapper для старого единственного источника."""
    return await fetch_public_posts(BATYA_CHANNEL, limit=limit)
