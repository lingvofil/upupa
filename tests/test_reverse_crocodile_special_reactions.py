import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


SPECIAL_MODES = ("reveal", "movie", "cartoon", "proverbs", "pun")


def _message(text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        message_id=77,
        text=text,
        from_user=SimpleNamespace(id=42, full_name="Игрок"),
    )


@pytest.mark.parametrize("mode", SPECIAL_MODES)
def test_close_guess_reacts_in_every_special_mode(monkeypatch, mode):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_guessing as guessing

    chat_id = "-100"
    reverse.games[chat_id] = {"word": "вентилятор", "mode": mode}
    react = AsyncMock()
    delegated = AsyncMock(return_value=False)
    monkeypatch.setattr(guessing.crocodile, "_safe_react_to_guess", react)
    monkeypatch.setattr(guessing.modes, "check_answer", delegated)

    msg = _message("вентилятр")
    try:
        assert asyncio.run(guessing.check_special_answer(msg)) is False
        react.assert_awaited_once_with(msg, guessing.crocodile.CLOSE_GUESS_REACTION)
        delegated.assert_not_awaited()
    finally:
        reverse.games.pop(chat_id, None)


@pytest.mark.parametrize("mode", SPECIAL_MODES)
def test_correct_guess_reacts_in_every_special_mode(monkeypatch, mode):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_guessing as guessing

    chat_id = "-100"
    reverse.games[chat_id] = {"word": "дирижер", "mode": mode}
    react = AsyncMock()
    delegated = AsyncMock(return_value=True)
    monkeypatch.setattr(guessing.crocodile, "_safe_react_to_guess", react)
    monkeypatch.setattr(guessing.modes, "check_answer", delegated)

    msg = _message("Мне кажется, это дирижёр!")
    try:
        assert asyncio.run(guessing.check_special_answer(msg)) is True
        react.assert_awaited_once_with(msg, guessing.crocodile.CORRECT_GUESS_REACTION)
        delegated.assert_awaited_once_with(msg)
    finally:
        reverse.games.pop(chat_id, None)
