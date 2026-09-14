import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from games import crocodile
from games import crocodile_modes as modes


def test_modes_callback_delegates_unknown_action_once():
    callback = SimpleNamespace(data="unrelated")
    downstream = AsyncMock(return_value="handled-downstream")

    result = asyncio.run(modes.handle_regular_callback(callback, downstream))

    assert result == "handled-downstream"
    downstream.assert_awaited_once_with(callback)


def test_modes_callback_intercepts_legacy_duo_action():
    chat_id = "-42"
    callback = SimpleNamespace(
        data=f"cr_duo_{chat_id}",
        answer=AsyncMock(return_value="game-ended"),
    )
    downstream = AsyncMock()
    original = crocodile.game_sessions.pop(chat_id, None)
    try:
        result = asyncio.run(modes.handle_regular_callback(callback, downstream))
    finally:
        if original is not None:
            crocodile.game_sessions[chat_id] = original

    assert result == "game-ended"
    callback.answer.assert_awaited_once_with("Игра уже закончилась")
    downstream.assert_not_awaited()
