"""Persistent rating helpers for Crocodile menu statistics."""

from __future__ import annotations

import html
import secrets
from pathlib import Path

from core.json_repository import JsonFileRepository
from core.paths import CROCODILE_STATE_PATH
from features import crocodile_scoring


LIKE_RATINGS_PATH = Path(CROCODILE_STATE_PATH).with_name("crocodile_like_ratings.json")
LEADERBOARD_TOP = 10
MAX_LIKE_TARGETS = 2000
_repository = JsonFileRepository(LIKE_RATINGS_PATH)


def _empty_state() -> dict:
    return {"targets": {}, "scores": {}}


def _load_state() -> dict:
    try:
        raw = _repository.load()
    except FileNotFoundError:
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    targets = raw.get("targets") if isinstance(raw.get("targets"), dict) else {}
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    return {"targets": targets, "scores": scores}


def _save_state(state: dict) -> None:
    _repository.save(state)


def normalize_artists(artists) -> list[dict]:
    """Normalize artist identities for durable like attribution."""
    normalized: list[dict] = []
    seen: set[str] = set()
    for row in artists or []:
        if isinstance(row, dict):
            raw_id = row.get("id")
            name = str(row.get("name") or "Художник")
        else:
            try:
                raw_id, name = row
            except (TypeError, ValueError):
                continue
            name = str(name or "Художник")
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue
        key = str(user_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"id": user_id, "name": name})
    return normalized


def create_like_target(chat_id: int | str, artists) -> str | None:
    """Create a short durable token embedded into a final drawing like button."""
    normalized = normalize_artists(artists)
    if not normalized:
        return None
    state = _load_state()
    targets = state.setdefault("targets", {})
    token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    while token in targets:
        token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
    targets[token] = {"chat_id": str(chat_id), "artists": normalized}
    while len(targets) > MAX_LIKE_TARGETS:
        targets.pop(next(iter(targets)), None)
    _save_state(state)
    return token


def get_like_target(token: str) -> dict | None:
    target = _load_state().get("targets", {}).get(str(token))
    return target if isinstance(target, dict) else None


def credit_like(token: str) -> bool:
    """Credit one unique drawing like to every artist attached to the target."""
    state = _load_state()
    target = state.get("targets", {}).get(str(token))
    if not isinstance(target, dict):
        return False
    chat_id = str(target.get("chat_id") or "")
    artists = normalize_artists(target.get("artists"))
    if not chat_id or not artists:
        return False
    table = state.setdefault("scores", {}).setdefault(chat_id, {})
    for artist in artists:
        uid = str(artist["id"])
        row = table.get(uid) if isinstance(table.get(uid), dict) else {}
        row["name"] = artist["name"]
        row["likes"] = max(0, int(row.get("likes", 0) or 0)) + 1
        table[uid] = row
    _save_state(state)
    return True


def format_like_leaderboard(chat_id: int | str) -> str:
    table = _load_state().get("scores", {}).get(str(chat_id), {})
    if not isinstance(table, dict) or not table:
        return (
            "❤️ Самые залайканные хуйдожники\n"
            "(рейтинг начнёт заполняться с новых рисунков, опубликованных после обновления)"
        )
    items = sorted(
        table.items(),
        key=lambda item: (-int((item[1] or {}).get("likes", 0)), str((item[1] or {}).get("name") or "")),
    )[:LEADERBOARD_TOP]
    lines = ["❤️ Самые залайканные хуйдожники"]
    for index, (_uid, row) in enumerate(items, 1):
        name = html.escape(str(row.get("name") or "художник").replace("@", "@\u200b"))
        lines.append(f"{index}. {name} — <b>{int(row.get('likes', 0))}</b> ❤️")
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин {secs:02d} сек"
    if minutes:
        return f"{minutes} мин {secs:02d} сек"
    return f"{secs} сек"


def format_longest_single_draw_leaderboard(chat_id: int | str) -> str:
    """Rank artists by their single longest successfully guessed drawing."""
    table = crocodile_scoring._load_artist_scores_sync().get(str(chat_id), {})
    timed = [
        (uid, row, float(row.get("max_guess_seconds", 0) or 0))
        for uid, row in table.items()
        if float(row.get("max_guess_seconds", 0) or 0) > 0
    ]
    if not timed:
        return "🐢 Рекордно долгие рисунки\n(пока нет завершённых угадыванием рисунков)"
    timed.sort(key=lambda item: (-item[2], str(item[1].get("name") or "")))
    lines = ["🐢 Рекордно долгие рисунки", "Самый долгий успешный раунд каждого художника:"]
    for index, (_uid, row, seconds) in enumerate(timed[:LEADERBOARD_TOP], 1):
        name = html.escape(str(row.get("name") or "художник").replace("@", "@\u200b"))
        lines.append(f"{index}. {name} — <b>{_format_duration(seconds)}</b>")
    return "\n".join(lines)
