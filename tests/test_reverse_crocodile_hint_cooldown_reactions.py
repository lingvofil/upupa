import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def _session(*, word="арбуз", hints=0, last_hint_at=None):
    return {
        "word": word,
        "difficulty": "medium",
        "hints": hints,
        "revealed_positions": set(),
        "image": b"image",
        "message_id": 10,
        "round_task": None,
        "started_at": 1000.0,
        "last_hint_at": last_hint_at,
    }


def _message(text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        message_id=77,
        text=text,
        from_user=SimpleNamespace(id=42, full_name="Игрок"),
    )


def test_reverse_crocodile_hint_cooldown_is_one_minute():
    import games.reverse_crocodile as reverse

    assert reverse.HINT_COOLDOWN_SECONDS == 60


def test_manual_and_automatic_hints_share_one_minute_cooldown(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session()
    reverse.games[chat_id] = session
    clock = {"now": 100.0}
    send_message = AsyncMock()
    monkeypatch.setattr(reverse.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(reverse, "bot", SimpleNamespace(send_message=send_message))

    async def scenario():
        assert await reverse._send_next_hint(chat_id, session) is True
        assert session["hints"] == 1
        assert session["last_hint_at"] == 100.0

        assert await reverse._send_next_hint(chat_id, session) is False
        clock["now"] = 159.9
        assert await reverse._send_next_hint(chat_id, session) is False

        clock["now"] = 160.0
        assert await reverse._send_next_hint(chat_id, session) is True
        assert session["hints"] == 2

    try:
        asyncio.run(scenario())
        assert send_message.await_count == 2
    finally:
        reverse.games.pop(chat_id, None)


def test_minute_tick_does_not_bypass_recent_manual_hint(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(last_hint_at=100.0)
    reverse.games[chat_id] = session
    send_message = AsyncMock()
    fake_bot = SimpleNamespace(
        send_message=send_message,
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=20)),
    )
    monkeypatch.setattr(reverse.time, "monotonic", lambda: 130.0)
    monkeypatch.setattr(reverse, "bot", fake_bot)
    monkeypatch.setattr(reverse.crocodile_game, "_safe_delete_message", AsyncMock())

    try:
        assert asyncio.run(reverse._run_round_tick(chat_id, session)) is True
        send_message.assert_not_awaited()
        assert session["hints"] == 0
        fake_bot.send_photo.assert_awaited_once()
    finally:
        reverse.games.pop(chat_id, None)


def test_close_guess_uses_regular_crocodile_reaction(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(word="вентилятор")
    reverse.games[chat_id] = session
    react = AsyncMock()
    monkeypatch.setattr(reverse.crocodile_game, "_safe_react_to_guess", react)

    msg = _message("вентилятр")
    try:
        assert asyncio.run(reverse.check_answer(msg)) is False
        react.assert_awaited_once_with(msg, reverse.crocodile_game.CLOSE_GUESS_REACTION)
    finally:
        reverse.games.pop(chat_id, None)


def test_correct_guess_uses_regular_crocodile_reaction(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(word="дирижер")
    reverse.games[chat_id] = session
    react = AsyncMock()
    finish = AsyncMock()
    add_point = Mock()
    monkeypatch.setattr(reverse.crocodile_game, "_safe_react_to_guess", react)
    monkeypatch.setattr(reverse, "_finish_game", finish)
    monkeypatch.setattr(reverse, "add_point", add_point)

    msg = _message("Мне кажется, это дирижёр!")
    try:
        assert asyncio.run(reverse.check_answer(msg)) is True
        react.assert_awaited_once_with(msg, reverse.crocodile_game.CORRECT_GUESS_REACTION)
        add_point.assert_called_once_with(chat_id, 42, "Игрок")
        finish.assert_awaited_once()
    finally:
        reverse.games.pop(chat_id, None)
