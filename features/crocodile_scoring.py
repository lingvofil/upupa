"""Separate Crocodile artist statistics and serialized answer handling."""

from __future__ import annotations

import asyncio
import html
import math
import time

from core.json_repository import JsonFileRepository
from core.paths import CROCODILE_ARTIST_SCORES_PATH
from games import crocodile


ARTIST_LEADERBOARD_TOP = 10
DRAW_PRIORITY_SECONDS = 5.0
_locks: dict[str, asyncio.Lock] = {}
_artist_repository = JsonFileRepository(CROCODILE_ARTIST_SCORES_PATH)
_artist_lock = asyncio.Lock()
_draw_priority_by_chat: dict[str, tuple[int, float]] = {}


def grant_draw_priority(chat_id: int | str, user_id: int, *, now: float | None = None) -> None:
    """Give the correct guesser a short exclusive window to claim the next drawing turn."""
    current = time.monotonic() if now is None else float(now)
    _draw_priority_by_chat[str(chat_id)] = (
        int(user_id),
        current + DRAW_PRIORITY_SECONDS,
    )


def can_claim_draw(chat_id: int | str, user_id: int, *, now: float | None = None) -> tuple[bool, int]:
    """Return whether a user may claim drawing now and seconds left for another user's priority."""
    cid = str(chat_id)
    priority = _draw_priority_by_chat.get(cid)
    if priority is None:
        return True, 0

    winner_id, deadline = priority
    current = time.monotonic() if now is None else float(now)
    if current >= deadline:
        _draw_priority_by_chat.pop(cid, None)
        return True, 0
    if int(user_id) == winner_id:
        return True, 0
    return False, max(1, math.ceil(deadline - current))


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
            if not isinstance(value, dict):
                continue
            normalized[str(chat_id)][str(user_id)] = {
                "successful_draws": max(0, int(value.get("successful_draws", 0))),
                "name": str(value.get("name") or ""),
            }
    return normalized


def _record_artist_success_sync(chat_id: str, user_id: int, user_name: str) -> int:
    scores = _load_artist_scores_sync()
    table = scores.setdefault(str(chat_id), {})
    row = table.setdefault(str(user_id), {"successful_draws": 0, "name": ""})
    row["successful_draws"] = int(row.get("successful_draws", 0)) + 1
    if user_name:
        row["name"] = str(user_name)
    _artist_repository.save(scores)
    return row["successful_draws"]


async def record_artist_success(chat_id: str, user_id: int, user_name: str) -> int:
    async with _artist_lock:
        return await asyncio.to_thread(
            _record_artist_success_sync,
            str(chat_id),
            int(user_id),
            str(user_name or ""),
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
        count = int(row.get("successful_draws", 0))
        lines.append(f"{index}. {name} — <b>{count}</b>")
    return "\n".join(lines)


async def check_regular_answer(message) -> bool:
    """Serialize correct guesses, record the artist, then run the legacy finish flow."""
    chat_id = str(message.chat.id)
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        session = crocodile.game_sessions.get(chat_id)
        if not session or not message.text:
            return False

        from_user = getattr(message, "from_user", None)
        is_drawer = bool(from_user and from_user.id == session.get("drawer_id"))
        correct = crocodile._contains_answer(message.text, session.get("word", "")) and not is_drawer
        if not correct:
            return await crocodile.check_answer(message)

        drawer_id = int(session.get("drawer_id") or 0)
        drawer_name = str(session.get("drawer_name") or "Художник")
        if drawer_id > 0:
            await record_artist_success(chat_id, drawer_id, drawer_name)

        if from_user:
            grant_draw_priority(chat_id, from_user.id)

        return await crocodile.check_answer(message)
