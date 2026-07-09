# === services/news.py — обзоры новостей: "чо по телеку", "новости футбола" ===
#
# Тянем свежие заголовки из публичных RSS-лент и суммируем их тем же
# промптом, что и команда "чотам" (PROMPTS_MEDIA), через активную модель чата.
# Обзор развёрнутый: отдельный абзац на каждую новость.

import asyncio
import logging
import random
import re
import xml.etree.ElementTree as ET

import requests
from aiogram import types

from prompts import PROMPTS_MEDIA

# Ленты пробуются по порядку, пока не наберётся MAX_ITEMS новостей
NEWS_FEEDS = [
    "https://lenta.ru/rss/last24",
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
]
FOOTBALL_FEEDS = [
    "https://www.championat.com/rss/news/football/",
    "https://www.sports.ru/rss/rubric.xml?s=208",  # рубрика "футбол"
]
MAX_ITEMS = 12

# Фильтр политики для "чо по телеку": новости с этими словами отбрасываются
# ещё до передачи модели (плюс дублирующая инструкция в промпте).
_POLITICS_RE = re.compile(
    r"путин|трамп|зеленск|украин|всу\b|\bсво\b|войн|фронт|обстрел|ракет"
    r"|дрон|бпла|санкци|госдум|кремл|белы[йм] дом|президент|премьер"
    r"|министр|мид\b|депутат|сенатор|губернатор|выбор[ыа]х?\b|парти[ия]"
    r"|нато|посол|диплома|оппозици|митинг|протест|мобилизац|военн|оборон|армия"
    r"|правозащит|замглав|чиновник|\bмэр|херсон|донецк|донбас|луганск"
    r"|запорожск|белгород|обесточ",
    re.IGNORECASE,
)


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


def _fetch_news_sync(feeds: list[str], exclude: re.Pattern | None = None) -> list[str]:
    news: list[str] = []
    for feed_url in feeds:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            items = _parse_rss(response.content)
            if exclude:
                kept = [item for item in items if not exclude.search(item)]
                logging.info(f"RSS {feed_url}: {len(kept)}/{len(items)} новостей после фильтра")
                items = kept
            else:
                logging.info(f"RSS {feed_url}: {len(items)} новостей")
            news.extend(items)
        except Exception as e:
            logging.warning(f"RSS {feed_url} недоступен: {e}")
        if len(news) >= MAX_ITEMS:
            break
    return news[:MAX_ITEMS]


def _build_review_prompt(news: list[str], topic_line: str, extra_rule: str = "") -> str:
    news_text = "\n".join(f"- {item}" for item in news)
    # В базовом промпте "чотам" зашит лимит "не более N слов" — для обзора
    # новостей его убираем: тут нужен развёрнутый ответ по абзацам.
    base_prompt = re.sub(r",?\s*не более \d+ слов", "", random.choice(PROMPTS_MEDIA))
    return (
        f"{base_prompt}.\n"
        f"Сделай обзор новостей ниже: выбери 5-8 самых интересных, "
        f"на каждую — отдельный абзац из 1-3 предложений, между абзацами пустая строка. "
        f"Всего не более 250 слов. Без вступлений и заключений, сразу обзор. {extra_rule}\n\n"
        f"{topic_line}:\n{news_text}"
    )


async def _process_news_review(
    message: types.Message,
    feeds: list[str],
    status_text: str,
    fail_text: str,
    topic_line: str,
    exclude: re.Pattern | None = None,
    extra_rule: str = "",
) -> None:
    # Ленивый импорт: избегаем цикла services.news <-> AI.talking
    from AI.talking import generate_simple_response

    status = await message.reply(status_text)
    try:
        news = await asyncio.to_thread(_fetch_news_sync, feeds, exclude)
        if not news:
            await status.edit_text(fail_text)
            return

        prompt = _build_review_prompt(news, topic_line, extra_rule)
        response_text = await generate_simple_response(prompt, str(message.chat.id))
        await status.delete()
        await message.reply(response_text)
    except Exception as e:
        logging.error(f"news review ({topic_line}): {e}", exc_info=True)
        try:
            await status.edit_text("Телек взорвался. Попробуй позже.")
        except Exception:
            pass


async def process_tv_news_command(message: types.Message) -> None:
    """Команда 'чо по телеку' / 'упупа новости' — обзор новостей без политики."""
    await _process_news_review(
        message,
        feeds=NEWS_FEEDS,
        status_text="Включаю телек...",
        fail_text="Телек не ловит, антенну сдуло. Попробуй позже.",
        topic_line="Последние новости для обзора",
        exclude=_POLITICS_RE,
        extra_rule="Политику, войну и чиновников не бери — только житейские, культурные, научные и прочие неполитические новости.",
    )


async def process_football_news_command(message: types.Message) -> None:
    """Команда 'новости футбола' — обзор последних футбольных новостей."""
    await _process_news_review(
        message,
        feeds=FOOTBALL_FEEDS,
        status_text="Включаю Матч ТВ...",
        fail_text="Футбол не ловит, все матчи отменили. Попробуй позже.",
        topic_line="Последние футбольные новости для обзора",
    )
