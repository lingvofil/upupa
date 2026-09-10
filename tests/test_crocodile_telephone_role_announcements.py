import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str, user_id: int, name: str = "Игрок & Co", chat_id: int = -42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=name),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_new_player_role_is_announced_with_real_mention(monkeypatch):
    from games import crocodile_telephone_role_announcements as announcements

    cid = "-42"
    game = {
        "phase": "lobby",
        "host_id": 101,
        "players": [(101, "Ведущий")],
        "telephone_roles": {"101": "text"},
    }
    announcements.crocodile_modes.telephone_games[cid] = game

    async def original(callback):
        game["players"].append((202, callback.from_user.full_name))
        game["telephone_roles"]["202"] = "draw"
        return "ok"

    monkeypatch.setattr(announcements, "_original_handle_telephone_callback", original)
    callback = _callback(f"ctel_role_draw_{cid}", 202)

    try:
        result = asyncio.run(announcements.telephone_callback_with_role_announcement(callback))
        assert result == "ok"
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.await_args.args[0]
        assert 'tg://user?id=202' in text
        assert "Игрок &amp; Co" in text
        assert "вступает в команду" in text
        assert "🎨 Только рисовать" in text
        assert callback.message.answer.await_args.kwargs["parse_mode"] == "HTML"
    finally:
        announcements.crocodile_modes.telephone_games.pop(cid, None)


def test_switch_between_roles_is_announced_as_team_change(monkeypatch):
    from games import crocodile_telephone_role_announcements as announcements

    cid = "-42"
    game = {
        "phase": "lobby",
        "host_id": 101,
        "players": [(101, "Ведущий"), (202, "Игрок")],
        "telephone_roles": {"101": "text", "202": "draw"},
    }
    announcements.crocodile_modes.telephone_games[cid] = game

    async def original(_callback):
        game["telephone_roles"]["202"] = "text"
        return "switched"

    monkeypatch.setattr(announcements, "_original_handle_telephone_callback", original)
    callback = _callback(f"ctel_role_text_{cid}", 202, name="Игрок")

    try:
        result = asyncio.run(announcements.telephone_callback_with_role_announcement(callback))
        assert result == "switched"
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.await_args.args[0]
        assert "Смена команды" in text
        assert "🎨 Только рисовать" in text
        assert "✍️ Только слова" in text
    finally:
        announcements.crocodile_modes.telephone_games.pop(cid, None)


def test_repeated_click_on_same_role_does_not_spam_chat(monkeypatch):
    from games import crocodile_telephone_role_announcements as announcements

    cid = "-42"
    game = {
        "phase": "lobby",
        "host_id": 101,
        "players": [(101, "Ведущий"), (202, "Игрок")],
        "telephone_roles": {"101": "text", "202": "draw"},
    }
    announcements.crocodile_modes.telephone_games[cid] = game
    original = AsyncMock(return_value="same")
    monkeypatch.setattr(announcements, "_original_handle_telephone_callback", original)
    callback = _callback(f"ctel_role_draw_{cid}", 202, name="Игрок")

    try:
        result = asyncio.run(announcements.telephone_callback_with_role_announcement(callback))
        assert result == "same"
        callback.message.answer.assert_not_awaited()
    finally:
        announcements.crocodile_modes.telephone_games.pop(cid, None)


def test_host_initial_role_is_announced_when_lobby_is_created(monkeypatch):
    from games import crocodile_telephone_role_announcements as announcements

    cid = "-42"
    announcements.crocodile_modes.telephone_games.pop(cid, None)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-42),
        from_user=SimpleNamespace(id=101, full_name="Ведущий"),
        answer=AsyncMock(),
    )

    async def original(_message):
        announcements.crocodile_modes.telephone_games[cid] = {
            "phase": "lobby",
            "host_id": 101,
            "players": [(101, "Ведущий")],
            "telephone_roles": {"101": "text"},
        }
        return "created"

    monkeypatch.setattr(announcements, "_original_start_telephone", original)

    try:
        result = asyncio.run(announcements.start_telephone_with_role_announcement(message))
        assert result == "created"
        message.answer.assert_awaited_once()
        text = message.answer.await_args.args[0]
        assert 'tg://user?id=101' in text
        assert "✍️ Только слова" in text
    finally:
        announcements.crocodile_modes.telephone_games.pop(cid, None)
