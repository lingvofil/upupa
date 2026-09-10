import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str, user_id: int, chat_id: int = -42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=f"User {user_id}"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _balanced_game():
    return {
        "phase": "lobby",
        "host_id": 101,
        "step": 0,
        "chain": [],
        "players": [
            (101, "Ведущий"),
            (202, "Слова 2"),
            (303, "Рисует 1"),
            (404, "Рисует 2"),
        ],
        "telephone_roles": {
            "101": "text",
            "202": "text",
            "303": "draw",
            "404": "draw",
        },
    }


def test_role_balance_requires_text_equal_to_draw_or_plus_one():
    from games import crocodile_telephone_roles as roles

    even = _balanced_game()
    assert roles.role_balance(even)[0] is True

    odd = _balanced_game()
    odd["players"].pop()
    odd["telephone_roles"].pop("404")
    assert roles.role_counts(odd) == (2, 1)
    assert roles.role_balance(odd)[0] is True

    too_many_text = _balanced_game()
    too_many_text["telephone_roles"]["303"] = "text"
    assert roles.role_counts(too_many_text) == (3, 1)
    assert roles.role_balance(too_many_text)[0] is False

    too_many_draw = _balanced_game()
    too_many_draw["telephone_roles"]["202"] = "draw"
    assert roles.role_counts(too_many_draw) == (1, 3)
    assert roles.role_balance(too_many_draw)[0] is False


def test_lobby_keyboard_has_two_explicit_role_groups():
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = _balanced_game()
    roles.crocodile_modes.telephone_games[cid] = game
    try:
        keyboard = roles.telephone_lobby_keyboard(cid)
        texts = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        assert "✍️ Только слова (2)" in texts
        assert "🎨 Только рисовать (2)" in texts
        assert f"ctel_role_text_{cid}" in callbacks
        assert f"ctel_role_draw_{cid}" in callbacks
        assert f"ctel_join_{cid}" not in callbacks
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_player_can_join_and_redistribute_between_groups(monkeypatch):
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = {
        "phase": "lobby",
        "host_id": 101,
        "players": [(101, "Ведущий")],
        "chain": [],
        "step": 0,
        "telephone_roles": {"101": "text"},
    }
    roles.crocodile_modes.telephone_games[cid] = game
    monkeypatch.setattr(roles, "_persist", lambda: None)

    try:
        draw_callback = _callback(f"ctel_role_draw_{cid}", 202)
        asyncio.run(roles.handle_telephone_callback_with_roles(draw_callback))
        assert (202, "User 202") in game["players"]
        assert game["telephone_roles"]["202"] == "draw"

        text_callback = _callback(f"ctel_role_text_{cid}", 202)
        asyncio.run(roles.handle_telephone_callback_with_roles(text_callback))
        assert game["telephone_roles"]["202"] == "text"
        assert len([row for row in game["players"] if row[0] == 202]) == 1
        text_callback.message.edit_text.assert_awaited_once()
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_start_is_blocked_until_role_groups_are_balanced(monkeypatch):
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = _balanced_game()
    game["telephone_roles"]["303"] = "text"
    roles.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock()
    monkeypatch.setattr(roles, "_original_handle_telephone_callback", original)
    monkeypatch.setattr(roles, "_persist", lambda: None)
    callback = _callback(f"ctel_start_{cid}", 101)

    try:
        asyncio.run(roles.handle_telephone_callback_with_roles(callback))
        callback.answer.assert_awaited_once()
        assert callback.answer.await_args.kwargs["show_alert"] is True
        original.assert_not_awaited()
        assert game["phase"] == "lobby"
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_balanced_start_interleaves_text_and_draw_roles(monkeypatch):
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = _balanced_game()
    roles.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock(return_value="started")
    monkeypatch.setattr(roles, "_original_handle_telephone_callback", original)
    monkeypatch.setattr(roles, "_persist", lambda: None)
    callback = _callback(f"ctel_start_{cid}", 101)

    try:
        result = asyncio.run(roles.handle_telephone_callback_with_roles(callback))
        assert result == "started"
        assert [row[0] for row in game["players"]] == [101, 303, 202, 404]
        assert [game["telephone_roles"][str(row[0])] for row in game["players"]] == [
            "text",
            "draw",
            "text",
            "draw",
        ]
        original.assert_awaited_once_with(callback)
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_admin_start_is_also_blocked_by_balance(monkeypatch):
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = _balanced_game()
    game["telephone_roles"]["303"] = "text"
    roles.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock()
    monkeypatch.setattr(roles, "_original_handle_telephone_callback", original)
    callback = _callback(f"ctel_start_{cid}", roles.ADMIN_ID)

    try:
        asyncio.run(roles.handle_telephone_callback_with_roles(callback))
        callback.answer.assert_awaited_once()
        original.assert_not_awaited()
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_role_aware_skip_never_gives_draw_turn_to_text_player(monkeypatch):
    from games import crocodile_telephone_roles as roles

    cid = "-42"
    game = {
        "phase": "playing",
        "host_id": 101,
        "step": 1,
        "chain": [{"kind": "text", "value": "кот"}],
        "players": [
            (101, "Text 1"),
            (201, "Draw 1"),
            (102, "Text 2"),
            (202, "Draw 2"),
            (103, "Text 3"),
        ],
        "telephone_roles": {
            "101": "text",
            "201": "draw",
            "102": "text",
            "202": "draw",
            "103": "text",
        },
    }
    roles.crocodile_modes.telephone_games[cid] = game
    monkeypatch.setattr(roles, "_persist", lambda: None)
    close_room = AsyncMock()
    send_message = AsyncMock()
    send_step = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr(roles.crocodile_party_controls, "_close_synthetic_room", close_room)
    monkeypatch.setattr(roles.crocodile_party_controls.bot, "send_message", send_message)
    monkeypatch.setattr(roles.crocodile_modes, "_send_telephone_step", send_step)
    monkeypatch.setattr(roles.crocodile_modes, "_finish_telephone", finish)

    try:
        result = asyncio.run(roles.skip_telephone_with_roles(cid, game))
        assert result == "Пропущен: Draw 1"
        assert [row[0] for row in game["players"]] == [101, 202, 102]
        assert game["telephone_roles"]["202"] == "draw"
        assert game["telephone_roles"]["102"] == "text"
        assert "103" not in game["telephone_roles"]
        send_step.assert_awaited_once_with(cid, game)
        finish.assert_not_awaited()
    finally:
        roles.crocodile_modes.telephone_games.pop(cid, None)


def test_legacy_lobby_roles_are_migrated_by_existing_parity():
    from games import crocodile_telephone_roles as roles

    game = {
        "phase": "lobby",
        "players": [(101, "A"), (202, "B"), (303, "C")],
    }
    assert roles.role_counts(game) == (2, 1)
    assert game["telephone_roles"] == {
        "101": "text",
        "202": "draw",
        "303": "text",
    }
