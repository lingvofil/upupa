# === services/news.py — "упупа чо по телеку": обзор последних новостей ===
#
# Тянем свежие заголовки из публичных RSS-лент и суммируем их тем же
# промптом, что и команда "чотам" (PROMPTS_MEDIA), через активную модель чата.

import asyncio
import logging
import random
import re
import xml.etree.ElementTree as ET

import requests
from aiogram import types

from prompts import PROMPTS_MEDIA

# Ленты пробуются по порядку, пока не наберётся MAX_ITEMS новостей
RSS_FEEDS = [
    "https://lenta.ru/rss/last24",
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
]
MAX_ITEMS = 12


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_rss(content: bytes) -> list[str]:
    items = []
    root = ET.fromstring(content)
    for item in root.iter("item"):
        title = _strip_html(item.findtext("title") or "")
        description = _strip_html(item.findtext("description") or "")
        if not title:
            continue
        items.append(f"{title}. {description}" if description else title)
    return items


def _fetch_news_sync() -> list[str]:
    news: list[str] = []
    for feed_url in RSS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            items = _parse_rss(response.content)
            logging.info(f"RSS {feed_url}: {len(items)} новостей")
            news.extend(items)
        except Exception as e:
            logging.warning(f"RSS {feed_url} недоступен: {e}")
        if len(news) >= MAX_ITEMS:
            break
    return news[:MAX_ITEMS]


async def process_tv_news_command(message: types.Message) -> None:
    """Команда 'упупа чо по телеку' — краткий обзор последних новостей."""
    # Ленивый импорт: избегаем цикла services.news <-> AI.talking
    from AI.talking import generate_simple_response

    status = await message.reply("Включаю телек...")
    try:
        news = await asyncio.to_thread(_fetch_news_sync)
        if not news:
            await status.edit_text("Телек не ловит, антенну сдуло. Попробуй позже.")
            return

        news_text = "\n".join(f"- {item}" for item in news)
        base_prompt = random.choice(PROMPTS_MEDIA)
        prompt = (
            f"{base_prompt}, не более 80 слов\n\n"
            f"Последние новости для обзора:\n{news_text}"
        )
        response_text = await generate_simple_response(prompt, str(message.chat.id))
        await status.delete()
        await message.reply(response_text)
    except Exception as e:
        logging.error(f"process_tv_news_command: {e}", exc_info=True)
        try:
            await status.edit_text("Телек взорвался. Попробуй позже.")
        except Exception:
            pass
