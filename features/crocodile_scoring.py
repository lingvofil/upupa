"""Crocodile artist statistics and serialized answer handling."""

from __future__ import annotations

import asyncio
import html
import time

from core.json_repository import JsonFileRepository
from core.paths import CROCODILE_ARTIST_SCORES_PATH
from games import crocodile


ARTIST_LEADERBOARD_TOP = 10
_locks: dict[str, asyncio.Lock] = {}
_artist_repository = JsonFileRepository(CROCODILE_ARTIST_SCORES_PATH)
_artist_lock = asyncio.Lock()


def _normalize_artist_row(value: dict) -> dict:
    return {
        "successful_draws": max(0, int(value.get("successful_draws", 0))),
        "name": str(value.get("name") or ""),
        "total_guess_seconds": max(0.0, float(value.get("total_guess_seconds", 0) or 0)),
        "timed_draws": max(0, int(value.get("timed_draws", 0) or 0)),
        "max_guess_seconds": max(0.0, float(value.get("max_guess_seconds", 0) or 0)),
    }


def _load_artist_scores_sync() -> dict[str, dict[str, dict]]:
    try:
        raw = _artist_repository.load()
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, dict]] = {}
    for chat_id, table in raw.items():
        if not isinstance(table, dict):
            continue
        normalized[str(chat_id)] = {}
        for user_id, value in table.items():
            if isinstance(value, dict):
                normalized[str(chat_id)][str(user_id)] = _normalize_artist_row(value)
    return normalized


def _record_artist_success_sync(
    chat_id: str,
    user_id: int,
    user_name: str,
    elapsed_seconds: float | None = None,
) -> int:
    scores = _load_artist_scores_sync()
    table = scores.setdefault(str(chat_id), {})
    row = table.setdefault(str(user_id), _normalize_artist_row({}))
    row["successful_draws"] = int(row.get("successful_draws", 0)) + 1
    if user_name:
        row["name"] = str(user_name)
    if elapsed_seconds is not None:
        elapsed = max(0.0, float(elapsed_seconds))
        row["total_guess_seconds"] = float(row.get("total_guess_seconds", 0)) + elapsed
        row["timed_draws"] = int(row.get("timed_draws", 0)) + 1
        row["max_guess_seconds"] = max(float(row.get("max_guess_seconds", 0)), elapsed)
    _artist_repository.save(scores)
    return int(row["successful_draws"])


async def record_artist_success(
    chat_id: str,
    user_id: int,
    user_name: str,
    *,
    elapsed_seconds: float | None = None,
) -> int:
    async with _artist_lock:
        return await asyncio.to_thread(
            _record_artist_success_sync,
            str(chat_id),
            int(user_id),
            str(user_name or ""),
            elapsed_seconds,
        )


def format_artist_leaderboard(chat_id: int | str) -> str:
    table = _load_artist_scores_sync().get(str(chat_id), {})
    if not table:
        return "🎨 Лучшие хуйдожники\n(пока никто ничего успешно не нарисовал)"
    items = sorted(
        table.items(),
        key=lambda item: (-int(item[1].get("successful_draws", 0)), str(item[1].get("name") or "")),
    )[:ARTIST_LEADERBOARD_TOP]
    lines = ["🎨 Лучшие хуйдожники"]
    for index, (_user_id, row) in enumerate(items, 1):
        name = html.escape(str(row.get("name") or "художник").replace("@", "@\u200b"))
        lines.append(f"{index}. {name} — <b>{int(row.get('successful_draws', 0))}</b>")
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {secs:02d} сек"
    return f"{secs} сек"


def format_slowest_artist_leaderboard(chat_id: int | str) -> str:
    """Rank artists by average time the chat needs to guess their successful drawings."""
    table = _load_artist_scores_sync().get(str(chat_id), {})
    timed = []
    for user_id, row in table.items():
        count = int(row.get("timed_draws", 0))
        if count <= 0:
            continue
        average = float(row.get("total_guess_seconds", 0)) / count
        timed.append((user_id, row, average))
    if not timed:
        return "🐌 Самые долго отгадываемые хуйдожники\n(пока недостаточно завершённых угадыванием рисунков)"
    timed.sort(key=lambda item: (-item[2], str(item[1].get("name") or "")))
    lines = ["🐌 Самые долго отгадываемые хуйдожники", "Среднее время до правильного ответа:"]
    for index, (_user_id, row, average) in enumerate(timed[:ARTIST_LEADERBOARD_TOP], 1):
        name = html.escape(str(row.get("name") or "художник").replace("@", "@\u200b"))
        samples = int(row.get("timed_draws", 0))
        lines.append(f"{index}. {name} — <b>{_format_duration(average)}</b> ({samples} рис.)")
    return "\n".join(lines)


def _artists(session: dict) -> list[tuple[int, str]]:
    ids = session.get("drawer_ids")
    names = session.get("drawer_names")
    if isinstance(ids, list) and ids:
        result = []
        for index, raw_id in enumerate(ids):
            try:
                user_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            name = (
                str(names[index])
                if isinstance(names, list) and index < len(names)
                else str(session.get("drawer_name") or "Художник")
            )
            if user_id > 0:
                result.append((user_id, name))
        if result:
            return result
    try:
        user_id = int(session.get("drawer_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    return [(user_id, str(session.get("drawer_name") or "Художник"))] if user_id > 0 else []


async def check_regular_answer(message) -> bool:
    """Serialize correct guesses, record every artist, then run the finish flow."""
    chat_id = str(message.chat.id)
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        session = crocodile.game_sessions.get(chat_id)
        if not session or not message.text:
            return False

        from_user = getattr(message, "from_user", None)
        artists = _artists(session)
        artist_ids = {user_id for user_id, _name in artists}
        is_drawer = bool(from_user and from_user.id in artist_ids)
        correct = crocodile._contains_answer(message.text, session.get("word", "")) and not is_drawer
        if not correct:
            return await crocodile.check_answer(message)

        started_at = float(session.get("started_at") or 0)
        elapsed = max(0.0, time.time() - started_at) if started_at > 0 else None
        for drawer_id, drawer_name in artists:
            await record_artist_success(
                chat_id,
                drawer_id,
                drawer_name,
                elapsed_seconds=elapsed,
            )

        return await crocodile.check_answer(message)
