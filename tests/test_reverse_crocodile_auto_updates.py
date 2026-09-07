import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def _session(*, hints=0, message_id=10):
    return {
        "word": "кот",
        "difficulty": "medium",
        "hints": hints,
        "image": b"image",
        "message_id": message_id,
        "round_task": None,
        "started_at": 1000.0,
    }


def test_reverse_crocodile_refreshes_every_minute():
    import games.reverse_crocodile as reverse

    assert reverse.ROUND_REFRESH_INTERVAL_SECONDS == 60


def test_minute_tick_sends_hint_then_bumps_round(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session()
    reverse.games[chat_id] = session
    send_message = AsyncMock()
    delete_message = AsyncMock()
    send_photo = AsyncMock(return_value=SimpleNamespace(message_id=20))
    monkeypatch.setattr(reverse.bot, "send_message", send_message)
    monkeypatch.setattr(reverse.crocodile_game, "_safe_delete_message", delete_message)
    monkeypatch.setattr(reverse.bot, "send_photo", send_photo)

    try:
        assert asyncio.run(reverse._run_round_tick(chat_id, session)) is True
        assert session["hints"] == 1
        send_message.assert_awaited_once_with(-100, "💡 В слове 3 букв(ы).")
        delete_message.assert_awaited_once_with(-100, 10)
        send_photo.assert_awaited_once()
        assert send_photo.await_args.kwargs["chat_id"] == -100
        assert send_photo.await_args.kwargs["reply_markup"] == reverse._keyboard(chat_id)
        assert session["message_id"] == 20
    finally:
        reverse.games.pop(chat_id, None)


def test_bump_continues_after_all_automatic_hints(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(hints=reverse.MAX_HINTS)
    reverse.games[chat_id] = session
    send_message = AsyncMock()
    delete_message = AsyncMock()
    send_photo = AsyncMock(return_value=SimpleNamespace(message_id=30))
    monkeypatch.setattr(reverse.bot, "send_message", send_message)
    monkeypatch.setattr(reverse.crocodile_game, "_safe_delete_message", delete_message)
    monkeypatch.setattr(reverse.bot, "send_photo", send_photo)

    try:
        assert asyncio.run(reverse._run_round_tick(chat_id, session)) is True
        send_message.assert_not_awaited()
        delete_message.assert_awaited_once_with(-100, 10)
        send_photo.assert_awaited_once()
        assert session["message_id"] == 30
    finally:
        reverse.games.pop(chat_id, None)


def test_finishing_round_cancels_minute_loop(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session()

    async def scenario():
        task = asyncio.create_task(asyncio.Event().wait())
        session["round_task"] = task
        reverse.games[chat_id] = session
        monkeypatch.setattr(reverse.bot, "send_message", AsyncMock())
        monkeypatch.setattr(reverse, "format_leaderboard", lambda *_args: "board")
        await reverse._finish_game(chat_id, "done")
        assert task.cancelled()
        assert chat_id not in reverse.games

    asyncio.run(scenario())
