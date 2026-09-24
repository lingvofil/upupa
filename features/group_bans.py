"""Persistent ban-list for Telegram groups the bot must not rejoin."""

import logging

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import GROUP_BANS_PATH

_group_bans: dict[str, dict] = {}


def _repository() -> JsonFileRepository:
    return JsonFileRepository(GROUP_BANS_PATH)


def load_group_bans(repository: JsonRepository | None = None) -> None:
    repo = repository or _repository()
    try:
        data = repo.load()
    except FileNotFoundError:
        _group_bans.clear()
        return
    except Exception as exc:
        logging.error("Failed to load group bans: %s", exc)
        _group_bans.clear()
        return
    _group_bans.clear()
    if isinstance(data, dict):
        _group_bans.update(data)


def save_group_bans(repository: JsonRepository | None = None) -> None:
    (repository or _repository()).save(_group_bans)


def ban_group(chat_id: int, title: str | None, username: str | None) -> None:
    _group_bans[str(chat_id)] = {
        "id": int(chat_id),
        "title": title,
        "username": username.lstrip("@") if username else None,
    }
    save_group_bans()


def is_group_banned(chat_id: int) -> bool:
    return str(chat_id) in _group_bans


def find_group_ban(identifier: str) -> dict | None:
    value = identifier.strip()
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        value = value.rstrip("/").rsplit("/", 1)[-1]
    folded = value.casefold()
    username = value.lstrip("@").casefold()
    for item in _group_bans.values():
        if value.lstrip("-").isdigit() and int(item["id"]) == int(value):
            return item
        item_username = (item.get("username") or "").casefold()
        if item_username and (
            item_username == username
            or f"@{item_username}" in folded
            or f"t.me/{item_username}" in folded
        ):
            return item
        item_title = (item.get("title") or "").casefold()
        if item_title and (item_title == folded or item_title in folded):
            return item
    return None


def unban_group(identifier: str) -> dict | None:
    item = find_group_ban(identifier)
    if item is None:
        return None
    _group_bans.pop(str(item["id"]), None)
    save_group_bans()
    return item
