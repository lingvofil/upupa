"""Chat announcements for broken-telephone role joins and switches."""

from __future__ import annotations

import html
from typing import Any

from games import crocodile_modes, crocodile_telephone_roles


_configured = False
_original_start_telephone = None
_original_handle_telephone_callback = None


def _user_id(value: Any) -> int | None:
    try:
        user_id = int(value or 0)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _requested_role(data: str) -> tuple[str | None, str | None]:
    if data.startswith("ctel_role_text_"):
        return crocodile_telephone_roles.ROLE_TEXT, data[len("ctel_role_text_"):]
    if data.startswith("ctel_role_draw_"):
        return crocodile_telephone_roles.ROLE_DRAW, data[len("ctel_role_draw_"):]
    return None, None


def _role_label(role: str) -> str:
    if role == crocodile_telephone_roles.ROLE_TEXT:
        return "✍️ Только слова"
    return "🎨 Только рисовать"


def _mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def role_announcement_text(
    user_id: int,
    name: str,
    role: str,
    *,
    previous_role: str | None = None,
) -> str:
    mention = _mention(user_id, name)
    current = html.escape(_role_label(role))
    if previous_role and previous_role != role:
        previous = html.escape(_role_label(previous_role))
        return (
            f"☎️ Смена команды: {mention} — "
            f"<b>{previous}</b> → <b>{current}</b>."
        )
    return f"☎️ {mention} вступает в команду <b>{current}</b>."


async def _send_announcement(message, text: str) -> None:
    sender = getattr(message, "answer", None)
    if callable(sender):
        await sender(text, parse_mode="HTML")


async def start_telephone_with_role_announcement(message) -> Any:
    """Announce the host's automatically selected initial role."""
    chat_id = str(message.chat.id)
    existed_before = chat_id in crocodile_modes.telephone_games
    result = await _original_start_telephone(message)
    if existed_before:
        return result

    game = crocodile_modes.telephone_games.get(chat_id)
    if not game or game.get("phase") != "lobby":
        return result

    host_id = _user_id(game.get("host_id"))
    if host_id is None:
        return result
    roles = crocodile_telephone_roles._ensure_roles(game)
    role = roles.get(str(host_id))
    if role not in {
        crocodile_telephone_roles.ROLE_TEXT,
        crocodile_telephone_roles.ROLE_DRAW,
    }:
        return result

    name = next(
        (
            str(row[1])
            for row in game.get("players", [])
            if isinstance(row, (list, tuple))
            and len(row) >= 2
            and _user_id(row[0]) == host_id
        ),
        str(getattr(getattr(message, "from_user", None), "full_name", None) or f"Игрок {host_id}"),
    )
    await _send_announcement(
        message,
        role_announcement_text(host_id, name, role),
    )
    return result


async def telephone_callback_with_role_announcement(callback) -> Any:
    """Announce a new role join or a real switch, but not a repeated click."""
    data = str(callback.data or "")
    requested_role, chat_id = _requested_role(data)
    if requested_role is None or chat_id is None:
        return await _original_handle_telephone_callback(callback)

    game = crocodile_modes.telephone_games.get(chat_id)
    user = getattr(callback, "from_user", None)
    user_id = _user_id(getattr(user, "id", None))
    if not game or game.get("phase") != "lobby" or user_id is None:
        return await _original_handle_telephone_callback(callback)

    player_ids = {
        _user_id(row[0])
        for row in game.get("players", [])
        if isinstance(row, (list, tuple)) and row
    }
    is_new = user_id not in player_ids
    roles_before = crocodile_telephone_roles._ensure_roles(game)
    previous_role = None if is_new else roles_before.get(str(user_id))

    result = await _original_handle_telephone_callback(callback)

    game_after = crocodile_modes.telephone_games.get(chat_id)
    if not game_after or game_after.get("phase") != "lobby":
        return result
    roles_after = crocodile_telephone_roles._ensure_roles(game_after)
    if roles_after.get(str(user_id)) != requested_role:
        return result
    if previous_role == requested_role:
        return result

    name = str(getattr(user, "full_name", None) or f"Игрок {user_id}")
    await _send_announcement(
        callback.message,
        role_announcement_text(
            user_id,
            name,
            requested_role,
            previous_role=previous_role,
        ),
    )
    return result


def configure_crocodile_telephone_role_announcements() -> None:
    """Wrap the final role-aware telephone handlers."""
    global _configured, _original_start_telephone, _original_handle_telephone_callback
    if _configured:
        return

    _original_start_telephone = crocodile_modes.start_telephone
    _original_handle_telephone_callback = crocodile_modes.handle_telephone_callback
    crocodile_modes.start_telephone = start_telephone_with_role_announcement
    crocodile_modes.handle_telephone_callback = telephone_callback_with_role_announcement
    _configured = True
