# === services/web_context.py — актуальная информация из интернета для диалогов ===
#
# У LLM-моделей бота устаревшая база знаний. Этот модуль детектит вопросы
# про недавние события (needs_web_search) и подтягивает свежие результаты
# веб-поиска (get_web_context), которые подмешиваются в промпт диалога.
#
# Поиск: Google Custom Search (те же ключи, что у "найди"),
# при сбое — fallback на DuckDuckGo HTML.

import asyncio
import logging
import re
from datetime import date

import requests
from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from config import GOOGLE_API_KEY, SEARCH_ENGINE_ID

MAX_RESULTS = 5

# Паттерны, по которым решаем, что вопрос про актуальные события
# и модели нужна свежая информация из интернета.
_WEB_TRIGGER_PATTERNS = [
    r"\b20(2[4-9]|[3-9]\d)\b",          # упоминание года 2024+
    r"загугл|погугл|в интернете|из интернета",
    r"новост",
    r"сегодня|вчера|позавчера|на днях|на этой неделе|в этом (году|месяце)",
    r"недавн|последн|свеж|актуальн",
    r"матч|чемпионат|турнир|финал|плей-?офф",
    r"сч[её]т игры|кто (выиграл|победил|проиграл)",
    r"выборы|избрали|президент",
    r"курс (доллара|евро|рубля|валют)|биткоин|биткойн",
    r"умер|погиб|скончал",
    r"случилось|произошло|происходит",
    r"вышел ли|уже вышл|дата (выхода|релиза)|релиз",
    r"сколько (сейчас )?стоит",
]
_WEB_TRIGGER_RE = re.compile("|".join(_WEB_TRIGGER_PATTERNS), re.IGNORECASE)


def needs_web_search(text: str) -> bool:
    """Похоже ли сообщение на вопрос об актуальных событиях."""
    if not text or len(text) < 8:
        return False
    return bool(_WEB_TRIGGER_RE.search(text))


def _search_google(query: str) -> list[dict]:
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=MAX_RESULTS).execute()
    return [
        {
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
        }
        for item in result.get("items", [])
    ]


def _search_duckduckgo(query: str) -> list[dict]:
    response = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    results = []
    for res in soup.select(".result")[:MAX_RESULTS]:
        title_el = res.select_one(".result__a")
        snippet_el = res.select_one(".result__snippet")
        if title_el:
            results.append({
                "title": title_el.get_text(strip=True),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })
    return results


def _web_search_sync(query: str) -> list[dict]:
    try:
        results = _search_google(query)
        if results:
            return results
        logging.info("Google CSE вернул пусто, пробую DuckDuckGo")
    except Exception as e:
        logging.warning(f"Google CSE недоступен ({e}), пробую DuckDuckGo")
    try:
        return _search_duckduckgo(query)
    except Exception as e:
        logging.error(f"DuckDuckGo тоже недоступен: {e}")
        return []


async def get_web_context(query: str) -> str:
    """Возвращает блок с результатами веб-поиска для вставки в промпт.

    Пустая строка — если поиск не дал результатов (промпт не меняется).
    """
    query = query.strip()[:200]
    if not query:
        return ""
    results = await asyncio.to_thread(_web_search_sync, query)
    if not results:
        return ""
    lines = [
        f"{i}. {r['title']}: {r['snippet']}" if r["snippet"] else f"{i}. {r['title']}"
        for i, r in enumerate(results, 1)
    ]
    return (
        f"\n\nСправка: сегодня {date.today().isoformat()}. "
        f"Свежие результаты поиска в интернете по теме сообщения:\n"
        + "\n".join(lines)
        + "\nЕсли вопрос касается недавних событий — опирайся на эти данные, "
        "а не на свою устаревшую память. Не упоминай, что тебе дали результаты поиска.\n"
    )
