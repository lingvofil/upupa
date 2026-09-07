import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def _session(*, word="котик", hints=0, message_id=10, revealed_positions=None):
    return {
        "word": word,
        "difficulty": "medium",
        "hints": hints,
        "revealed_positions": set(revealed_positions or ()),
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
    delete_message = AsyncMock()
    fake_bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=20)),
    )
    monkeypatch.setattr(reverse, "bot", fake_bot)
    monkeypatch.setattr(reverse.crocodile_game, "_safe_delete_message", delete_message)

    try:
        assert asyncio.run(reverse._run_round_tick(chat_id, session)) is True
        assert session["hints"] == 1
        fake_bot.send_message.assert_awaited_once_with(-100, "💡 В слове 5 букв(ы).")
        delete_message.assert_awaited_once_with(-100, 10)
        fake_bot.send_photo.assert_awaited_once()
        assert fake_bot.send_photo.await_args.kwargs["chat_id"] == -100
        assert fake_bot.send_photo.await_args.kwargs["reply_markup"] == reverse._keyboard(chat_id)
        assert session["message_id"] == 20
    finally:
        reverse.games.pop(chat_id, None)


def test_third_stage_reveals_one_new_random_position_at_a_time(monkeypatch):
    import games.reverse_crocodile as reverse

    session = _session(word="арбуз", hints=2)
    monkeypatch.setattr(reverse.random, "choice", lambda positions: positions[1])

    first = reverse._make_next_hint(session)
    assert first == "💡 Ещё одна буква: А ▪️ Б ▪️ ▪️"
    assert session["revealed_positions"] == {2}

    second = reverse._make_next_hint(session)
    assert second == "💡 Ещё одна буква: А ▪️ Б У ▪️"
    assert session["revealed_positions"] == {2, 3}


def test_progressive_hints_never_reveal_the_entire_word(monkeypatch):
    import games.reverse_crocodile as reverse

    session = _session(word="арбуз", hints=2)
    monkeypatch.setattr(reverse.random, "choice", lambda positions: positions[0])

    emitted = []
    while reverse._has_next_hint(session):
        emitted.append(reverse._make_next_hint(session))
        session["hints"] += 1

    assert len(emitted) == 3
    assert len(session["revealed_positions"]) == 3
    assert reverse._has_next_hint(session) is False
    assert emitted[-1].count("▪️") == 1


def test_failed_progressive_hint_send_does_not_consume_letter(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(word="арбуз", hints=2)
    reverse.games[chat_id] = session
    monkeypatch.setattr(reverse.random, "choice", lambda positions: positions[0])
    monkeypatch.setattr(
        reverse,
        "bot",
        SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("telegram down"))),
    )

    try:
        with pytest.raises(RuntimeError, match="telegram down"):
            asyncio.run(reverse._send_next_hint(chat_id, session))
        assert session["hints"] == 2
        assert session["revealed_positions"] == set()
    finally:
        reverse.games.pop(chat_id, None)


def test_bump_continues_after_progressive_hints_are_exhausted(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    session = _session(word="кот", hints=3, revealed_positions={1})
    reverse.games[chat_id] = session
    delete_message = AsyncMock()
    fake_bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=30)),
    )
    monkeypatch.setattr(reverse, "bot", fake_bot)
    monkeypatch.setattr(reverse.crocodile_game, "_safe_delete_message", delete_message)

    try:
        assert reverse._has_next_hint(session) is False
        assert asyncio.run(reverse._run_round_tick(chat_id, session)) is True
        fake_bot.send_message.assert_not_awaited()
        delete_message.assert_awaited_once_with(-100, 10)
        fake_bot.send_photo.assert_awaited_once()
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
        monkeypatch.setattr(reverse, "bot", SimpleNamespace(send_message=AsyncMock()))
        monkeypatch.setattr(reverse, "format_leaderboard", lambda *_args: "board")
        await reverse._finish_game(chat_id, "done")
        assert task.cancelled()
        assert chat_id not in reverse.games

    asyncio.run(scenario())
