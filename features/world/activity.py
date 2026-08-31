"""Recent citizen activity derived from the existing Telegram message log."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import re

from core.paths import USER_MESSAGES_LOG_PATH


_LOG_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) "
    r"- User (\d+) \((.*?)\) \[(.*?)\]: (.*?)$"
)


def _top_active_sync(
    chat_id: int | str,
    *,
    days: int = 7,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> tuple[str, int] | None:
    threshold = (now or datetime.now()) - timedelta(days=days)
    counter: Counter[str] = Counter()
    path = Path(log_file_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                match = _LOG_RE.search(line)
                if match is None:
                    continue
                timestamp_str, log_chat_id, _chat_name, _user_id, username, display_name, text = match.groups()
                if str(log_chat_id) != str(chat_id) or not text.strip():
                    continue
                try:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f")
                except ValueError:
                    continue
                if timestamp < threshold:
                    continue
                name = (display_name or "").strip()
                if not name or name.lower() in {"none", "null"}:
                    name = (username or "").strip()
                if not name or name.lower() in {"none", "null"}:
                    name = "Безымянный гражданин"
                counter[name] += 1
    except FileNotFoundError:
        return None

    if not counter:
        return None
    return counter.most_common(1)[0]


async def get_top_active_citizen(
    chat_id: int | str,
    *,
    days: int = 7,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> tuple[str, int] | None:
    return await asyncio.to_thread(
        _top_active_sync,
        chat_id,
        days=days,
        log_file_path=log_file_path,
        now=now,
    )
