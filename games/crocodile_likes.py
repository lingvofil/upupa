"""Unique likes for finished Crocodile drawings."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.loader import bot
from core.paths import CROCODILE_STATE_PATH


LIKES_FILE = Path(CROCODILE_STATE_PATH).with_name("crocodile_likes.json")
MAX_LIKE_RECORDS = 2000

_like_registry: dict[str, dict[str, Any]] | None = None
_like_lock = asyncio.Lock()


def _message_key(chat_id: int | str, message_id: int | str) -> str:
    return f"{chat_id}:{message_id}"


def _button_like_count(reply_markup: Any) -> int:
    try:
        button = reply_markup.inline_keyboard[0][0]
        match = re.search(r"\d+", button.text or "")
        return int(match.group(0)) if match else 0
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0


def _normalize_registry(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for key, record in raw.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            continue
        try:
            base_count = max(0, int(record.get("base_count", 0)))
        except (TypeError, ValueError):
            base_count = 0
        users_raw = record.get("users", [])
        users = []
        if isinstance(users_raw, list):
            for user_id in users_raw:
                try:
                    users.append(int(user_id))
                except (TypeError, ValueError):
                    continue
        normalized[key] = {
            "base_count": base_count,
            "users": list(dict.fromkeys(users)),
        }
    return normalized


def _load_registry() -> dict[str, dict[str, Any]]:
    try:
        if not LIKES_FILE.exists():
            return {}
        with LIKES_FILE.open("r", encoding="utf-8") as file:
            return _normalize_registry(json.load(file))
    except Exception as exc:
        logging.warning("[crocodile] failed to load like registry: %s", exc)
        return {}


def _get_registry() -> dict[str, dict[str, Any]]:
    global _like_registry
    if _like_registry is None:
        _like_registry = _load_registry()
    return _like_registry


def _save_registry(registry: dict[str, dict[str, Any]]) -> None:
    tmp_path = LIKES_FILE.with_suffix(LIKES_FILE.suffix + ".tmp")
    try:
        LIKES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(registry, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, LIKES_FILE)
    except Exception as exc:
        logging.warning("[crocodile] failed to save like registry: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _register_like(
    registry: dict[str, dict[str, Any]],
    *,
    key: str,
    user_id: int,
    current_count: int,
) -> tuple[bool, int]:
    record = registry.pop(key, None)
    if record is None:
        record = {"base_count": max(0, int(current_count)), "users": []}

    users = record.setdefault("users", [])
    if user_id in users:
        registry[key] = record
        return False, int(record.get("base_count", 0)) + len(users)

    users.append(user_id)
    registry[key] = record
    while len(registry) > MAX_LIKE_RECORDS:
        registry.pop(next(iter(registry)), None)

    return True, int(record.get("base_count", 0)) + len(users)


def _rollback_like(registry: dict[str, dict[str, Any]], *, key: str, user_id: int) -> None:
    record = registry.get(key)
    if not record:
        return
    users = record.get("users", [])
    if user_id in users:
        users.remove(user_id)
    if not users and int(record.get("base_count", 0)) == 0:
        registry.pop(key, None)


async def handle_like_callback(callback) -> None:
    """Apply at most one like per Telegram user to one final drawing message."""
    if not callback.message or not callback.from_user:
        await callback.answer("Не удалось определить лайк :(")
        return

    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    user_id = callback.from_user.id
    key = _message_key(chat_id, message_id)

    async with _like_lock:
        registry = _get_registry()
        current_count = _button_like_count(callback.message.reply_markup)
        added, new_count = _register_like(
            registry,
            key=key,
            user_id=user_id,
            current_count=current_count,
        )
        if not added:
            await callback.answer("Ты уже лайкал этот рисунок")
            return

        from games.crocodile import get_end_game_keyboard

        try:
            await callback.message.edit_reply_markup(
                reply_markup=get_end_game_keyboard(new_count)
            )
        except Exception:
            _rollback_like(registry, key=key, user_id=user_id)
            raise

        _save_registry(registry)

    try:
        await bot.send_message(
            chat_id,
            f"❤️ **{callback.from_user.full_name}** поставил лайк хуйдожнику!",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logging.warning("[crocodile] like announcement failed: %s", exc)

    await callback.answer("Лайк поставлен!")
