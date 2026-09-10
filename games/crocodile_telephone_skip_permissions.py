"""Permission gate for skipping the current broken-telephone player."""

from __future__ import annotations

from typing import Any

from core.settings import ADMIN_ID
from games import crocodile_modes, crocodile_party_controls


_configured = False
_original_telephone_callback = None
_original_menu_callback = None


def _callback_user_id(callback) -> int | None:
    return getattr(getattr(callback, "from_user", None), "id", None)


def can_skip_telephone_player(game: dict, user_id: int | str | None) -> bool:
    """Only the host, current player or bot owner may skip the current turn."""
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        return False
    if uid <= 0:
        return False

    try:
        if uid == int(ADMIN_ID):
            return True
    except (TypeError, ValueError):
        pass

    try:
        if uid == int(game.get("host_id") or 0):
            return True
    except (TypeError, ValueError):
        pass

    try:
        step = int(game.get("step") or 0)
        players = game.get("players") or []
        if 0 <= step < len(players):
            return uid == int(players[step][0])
    except (TypeError, ValueError, IndexError):
        return False
    return False


async def telephone_callback_with_skip_permissions(callback) -> Any:
    """Gate the direct ``ctel_skip_*`` button before existing wrappers."""
    data = callback.data or ""
    if data.startswith("ctel_skip_"):
        chat_id = data[len("ctel_skip_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if game and game.get("phase") == "playing":
            if not can_skip_telephone_player(game, _callback_user_id(callback)):
                return await callback.answer(
                    "Пропустить может только ведущий или текущий игрок",
                    show_alert=True,
                )
    return await _original_telephone_callback(callback)


async def menu_callback_with_skip_permissions(callback) -> Any:
    """Apply the same rule to the unified-menu skip button."""
    if (callback.data or "") != "cmenu_skip":
        return await _original_menu_callback(callback)

    chat_id = str(callback.message.chat.id)
    game = crocodile_modes.telephone_games.get(chat_id)
    if not game or game.get("phase") != "playing":
        return await _original_menu_callback(callback)

    user_id = _callback_user_id(callback)
    if not can_skip_telephone_player(game, user_id):
        return await callback.answer(
            "Пропустить может только ведущий или текущий игрок",
            show_alert=True,
        )

    # The old menu handler admits only chain participants. ADMIN_ID is an
    # operational override and may intentionally be outside the chain, so the
    # admin path must perform the existing skip operation directly.
    try:
        is_admin = int(user_id or 0) == int(ADMIN_ID)
    except (TypeError, ValueError):
        is_admin = False
    if is_admin:
        await callback.answer("Пропускаем (админ)")
        await crocodile_party_controls._skip_telephone(chat_id, game)
        return

    return await _original_menu_callback(callback)


def configure_crocodile_telephone_skip_permissions() -> None:
    """Install the permission gate after admin/UI/resilience wrappers."""
    global _configured, _original_telephone_callback, _original_menu_callback
    if _configured:
        return

    _original_telephone_callback = crocodile_modes.handle_telephone_callback
    _original_menu_callback = crocodile_party_controls.handle_menu_callback
    crocodile_modes.handle_telephone_callback = telephone_callback_with_skip_permissions
    crocodile_party_controls.handle_menu_callback = menu_callback_with_skip_permissions

    from games.crocodile_telephone_roles import configure_crocodile_telephone_roles

    configure_crocodile_telephone_roles()

    from games.crocodile_telephone_role_announcements import (
        configure_crocodile_telephone_role_announcements,
    )

    configure_crocodile_telephone_role_announcements()
    _configured = True
