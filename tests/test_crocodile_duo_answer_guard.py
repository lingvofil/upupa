import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _message(user_id: int, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-42),
        message_id=777,
        text=text,
        from_user=SimpleNamespace(id=user_id, full_name=f"Игрок {user_id}"),
    )


def test_second_duo_artist_cannot_finish_round_as_guesser(monkeypatch):
    from features import crocodile_scoring as scoring

    chat_id = "-42"
    scoring._locks.clear()
    scoring.crocodile.game_sessions[chat_id] = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }
    lower_check = AsyncMock(return_value=True)
    monkeypatch.setattr(scoring.crocodile, "check_answer", lower_check)

    try:
        handled = asyncio.run(scoring.check_regular_answer(_message(2, "барсук")))
    finally:
        scoring.crocodile.game_sessions.pop(chat_id, None)

    assert handled is True
    lower_check.assert_not_awaited()


def test_second_duo_artist_regular_message_does_not_touch_guess_flow(monkeypatch):
    from features import crocodile_scoring as scoring

    chat_id = "-42"
    scoring._locks.clear()
    scoring.crocodile.game_sessions[chat_id] = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }
    lower_check = AsyncMock(return_value=True)
    monkeypatch.setattr(scoring.crocodile, "check_answer", lower_check)

    try:
        handled = asyncio.run(scoring.check_regular_answer(_message(2, "рисуй уши побольше")))
    finally:
        scoring.crocodile.game_sessions.pop(chat_id, None)

    assert handled is False
    lower_check.assert_not_awaited()


def test_non_artist_correct_guess_still_reaches_finish_flow(monkeypatch):
    from features import crocodile_scoring as scoring

    chat_id = "-42"
    scoring._locks.clear()
    scoring.crocodile.game_sessions[chat_id] = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }
    lower_check = AsyncMock(return_value=True)
    monkeypatch.setattr(scoring.crocodile, "check_answer", lower_check)
    monkeypatch.setattr(scoring, "record_artist_success", AsyncMock(return_value=1))
    monkeypatch.setattr(scoring, "grant_draw_priority", lambda *args, **kwargs: None)

    message = _message(3, "это барсук")
    try:
        handled = asyncio.run(scoring.check_regular_answer(message))
    finally:
        scoring.crocodile.game_sessions.pop(chat_id, None)

    assert handled is True
    lower_check.assert_awaited_once_with(message)
