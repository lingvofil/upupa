"""Персистентная история канала, расписание и внутреннее настроение."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

POSTS_FILE = Path("channel_posts.json")
SCHEDULE_FILE = Path("channel_schedule.json")
MOOD_FILE = Path("channel_mood.json")


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_posts(limit: int | None = None) -> list[dict]:
    data = _read_json(POSTS_FILE, [])
    if not isinstance(data, list):
        return []
    posts = [item for item in data if isinstance(item, dict)]
    if limit is not None:
        return posts[-limit:]
    return posts


def append_post(post: dict) -> None:
    posts = load_posts()
    posts.append(post)
    _atomic_write_json(POSTS_FILE, posts)


def load_schedule() -> dict:
    data = _read_json(SCHEDULE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_schedule(state: dict) -> None:
    _atomic_write_json(SCHEDULE_FILE, state)


def load_mood() -> dict:
    data = _read_json(MOOD_FILE, {})
    return data if isinstance(data, dict) else {}


def save_mood(state: dict) -> None:
    _atomic_write_json(MOOD_FILE, state)
