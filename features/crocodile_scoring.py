"""Extended Crocodile scoring: fast-guess bonus and separate artist statistics."""

from __future__ import annotations

import asyncio
import html
import time

from core.json_repository import JsonFileRepository
from core.paths import CROCODILE_ARTIST_SCORES_PATH
from games import crocodile


FAST_BONUS_2_SECONDS = 30
FAST_BONUS_1_SECONDS = 60
ARTIST_LEADERBOARD_TOP = 10

_round_started_at: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}
_artist_repository = JsonFileRepository(CROCODILE_ARTIST_SCORES_PATH)
_artist_lock = asyncio.Lock()


def mark_round_started(chat_id: int | str, *, started_at: float | None = None) -> None:
    _round_started_at[str(chat_id)] = started_at if started_at is not None else time.monotonic()


def clear_round_started(chat_id: int | str) -> None:
    _round_started_at.pop(str(chat_id), None)


def fast_guess_bonus(elapsed_seconds: float | None) -> int:
    if elapsed_seconds is None or elapsed_seconds < 0:
        return 0
    if elapsed_seconds <= FAST_BONUS_2_SECONDS:
        return 2
    if elapsed_seconds <= FAST_BONUS_1_SECONDS:
        return 1
    return 0


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
    """Serialize correct guesses, pre-award speed bonus, then run legacy finish flow."""
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

        started = _round_started_at.get(chat_id)
        elapsed = (time.monotonic() - started) if started is not None else None
        bonus = fast_guess_bonus(elapsed)
        if from_user:
            for _ in range(bonus):
                crocodile.add_point(chat_id, from_user.id, from_user.full_name)

        drawer_id = int(session.get("drawer_id") or 0)
        drawer_name = str(session.get("drawer_name") or "Художник")
        if drawer_id > 0:
            await record_artist_success(chat_id, drawer_id, drawer_name)

        handled = await crocodile.check_answer(message)
        clear_round_started(chat_id)
        if handled and bonus:
            seconds_text = f" за {elapsed:.0f} сек." if elapsed is not None else ""
            await message.answer(f"⚡ Быстрое угадывание{seconds_text}: +{bonus} бонусных очк.")
        return handled
