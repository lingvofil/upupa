import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_draw_priority_blocks_others_for_five_seconds():
    import features.crocodile_scoring as scoring

    scoring._draw_priority_by_chat.clear()
    scoring.grant_draw_priority("-100", 10, now=100.0)

    assert scoring.can_claim_draw("-100", 10, now=100.0) == (True, 0)
    assert scoring.can_claim_draw("-100", 20, now=100.0) == (False, 5)
    assert scoring.can_claim_draw("-100", 20, now=104.1) == (False, 1)
    assert scoring.can_claim_draw("-100", 20, now=105.0) == (True, 0)
    assert "-100" not in scoring._draw_priority_by_chat


def test_correct_guesser_receives_draw_priority(monkeypatch):
    import features.crocodile_scoring as scoring

    chat_id = "-100"
    scoring._locks.clear()
    scoring._draw_priority_by_chat.clear()
    scoring.crocodile.game_sessions[chat_id] = {
        "word": "кот",
        "drawer_id": 10,
        "drawer_name": "Художник",
        "started_at": 100.0,
    }
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        text="это кот",
        from_user=SimpleNamespace(id=20),
    )
    record_artist = AsyncMock(return_value=1)
    finish_round = AsyncMock(return_value=True)
    grant_priority = Mock()
    monkeypatch.setattr(scoring, "record_artist_success", record_artist)
    monkeypatch.setattr(scoring.crocodile, "check_answer", finish_round)
    monkeypatch.setattr(scoring, "grant_draw_priority", grant_priority)

    try:
        assert asyncio.run(scoring.check_regular_answer(message)) is True
    finally:
        scoring.crocodile.game_sessions.pop(chat_id, None)

    grant_priority.assert_called_once_with(chat_id, 20)
    finish_round.assert_awaited_once_with(message)


def _draw_callback(user_id: int):
    return SimpleNamespace(
        data="btn_want_draw",
        message=SimpleNamespace(chat=SimpleNamespace(id=-100)),
        from_user=SimpleNamespace(id=user_id, full_name=f"Игрок {user_id}"),
        answer=AsyncMock(),
    )


def test_want_draw_rejects_non_winner_during_priority(monkeypatch):
    import handlers.games as games

    callback = _draw_callback(20)
    handle_callback = AsyncMock()
    monkeypatch.setattr(games, "can_claim_draw", Mock(return_value=(False, 3)))
    monkeypatch.setattr(games.crocodile, "handle_callback", handle_callback)

    asyncio.run(games.croc_callback(callback))

    callback.answer.assert_awaited_once_with(
        "Сначала рисовать может тот, кто угадал. Ещё 3 сек.",
        show_alert=True,
    )
    handle_callback.assert_not_awaited()


def test_winner_can_claim_draw_immediately(monkeypatch):
    import handlers.games as games

    callback = _draw_callback(20)
    handle_callback = AsyncMock()
    monkeypatch.setattr(games, "can_claim_draw", Mock(return_value=(True, 0)))
    monkeypatch.setattr(games.crocodile, "handle_callback", handle_callback)

    asyncio.run(games.croc_callback(callback))

    handle_callback.assert_awaited_once_with(callback)
