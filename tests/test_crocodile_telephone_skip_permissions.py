import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str, user_id: int, chat_id: int = -42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=f"User {user_id}"),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
    )


def _game(admin_id: int):
    return {
        "phase": "playing",
        "host_id": 101,
        "step": 1,
        "players": [
            (101, "Ведущий"),
            (202, "Текущий"),
            (303, "Другой участник"),
        ],
        "chain": [],
        "admin_id_for_test": admin_id,
    }


def test_skip_permission_allows_host_current_player_and_admin():
    from games import crocodile_telephone_skip_permissions as permissions

    game = _game(permissions.ADMIN_ID)

    assert permissions.can_skip_telephone_player(game, 101) is True
    assert permissions.can_skip_telephone_player(game, 202) is True
    assert permissions.can_skip_telephone_player(game, permissions.ADMIN_ID) is True


def test_skip_permission_denies_other_participant_and_outsider():
    from games import crocodile_telephone_skip_permissions as permissions

    game = _game(permissions.ADMIN_ID)

    assert permissions.can_skip_telephone_player(game, 303) is False
    assert permissions.can_skip_telephone_player(game, 404) is False


def test_direct_skip_blocks_non_current_participant(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock()
    monkeypatch.setattr(permissions, "_original_telephone_callback", original)
    callback = _callback(f"ctel_skip_{cid}", 303)

    try:
        asyncio.run(permissions.telephone_callback_with_skip_permissions(callback))
        callback.answer.assert_awaited_once_with(
            "Пропустить может только ведущий или текущий игрок",
            show_alert=True,
        )
        original.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_direct_skip_delegates_for_host_current_and_admin(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock(return_value="delegated")
    monkeypatch.setattr(permissions, "_original_telephone_callback", original)

    try:
        for user_id in (101, 202, permissions.ADMIN_ID):
            original.reset_mock()
            callback = _callback(f"ctel_skip_{cid}", user_id)
            result = asyncio.run(
                permissions.telephone_callback_with_skip_permissions(callback)
            )
            assert result == "delegated"
            original.assert_awaited_once_with(callback)
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_skip_blocks_non_current_participant(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock()
    monkeypatch.setattr(permissions, "_original_menu_callback", original)
    callback = _callback("cmenu_skip", 303)

    try:
        asyncio.run(permissions.menu_callback_with_skip_permissions(callback))
        callback.answer.assert_awaited_once_with(
            "Пропустить может только ведущий или текущий игрок",
            show_alert=True,
        )
        original.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_skip_delegates_for_host_and_current_player(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock(return_value="delegated")
    monkeypatch.setattr(permissions, "_original_menu_callback", original)

    try:
        for user_id in (101, 202):
            original.reset_mock()
            callback = _callback("cmenu_skip", user_id)
            result = asyncio.run(permissions.menu_callback_with_skip_permissions(callback))
            assert result == "delegated"
            original.assert_awaited_once_with(callback)
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_skip_admin_can_override_without_being_participant(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock()
    skip = AsyncMock()
    monkeypatch.setattr(permissions, "_original_menu_callback", original)
    monkeypatch.setattr(permissions.crocodile_party_controls, "_skip_telephone", skip)
    callback = _callback("cmenu_skip", permissions.ADMIN_ID)

    try:
        asyncio.run(permissions.menu_callback_with_skip_permissions(callback))
        callback.answer.assert_awaited_once_with("Пропускаем (админ)")
        skip.assert_awaited_once_with(cid, game)
        original.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)
