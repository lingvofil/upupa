import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_reverse_crocodile_keyboard_has_no_alternate_image_button():
    import games.reverse_crocodile as reverse

    markup = reverse._keyboard("-100")
    texts = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "🔄 Другая картинка" not in texts
    assert not any(callback and callback.startswith("rcroc_img_") for callback in callbacks)
    assert "💡 Подсказка" in texts
    assert "🏳️ Сдаёмся" in texts


def test_reverse_crocodile_surrender_opens_after_exactly_five_minutes():
    import games.reverse_crocodile as reverse

    session = {"started_at": 1000.0}

    assert reverse._surrender_remaining_seconds(session, now=1000.0) == 300
    assert reverse._surrender_remaining_seconds(session, now=1299.1) == 1
    assert reverse._surrender_remaining_seconds(session, now=1300.0) == 0
    assert reverse._surrender_remaining_seconds(session, now=1400.0) == 0


def test_reverse_crocodile_rejects_early_surrender(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    reverse.games[chat_id] = {
        "word": "кот",
        "hints": 0,
        "image": b"image",
        "started_at": 1000.0,
    }
    monkeypatch.setattr(reverse.time, "monotonic", lambda: 1120.0)
    finish = AsyncMock()
    monkeypatch.setattr(reverse, "_finish_game", finish)
    cb = SimpleNamespace(
        data=f"rcroc_stop_{chat_id}",
        answer=AsyncMock(),
        message=SimpleNamespace(),
    )

    try:
        asyncio.run(reverse.handle_callback(cb))
        cb.answer.assert_awaited_once_with("Сдаться можно через 3:00.", show_alert=True)
        finish.assert_not_awaited()
    finally:
        reverse.games.pop(chat_id, None)


def test_reverse_crocodile_allows_surrender_after_five_minutes(monkeypatch):
    import games.reverse_crocodile as reverse

    chat_id = "-100"
    reverse.games[chat_id] = {
        "word": "кот",
        "hints": 0,
        "image": b"image",
        "started_at": 1000.0,
    }
    monkeypatch.setattr(reverse.time, "monotonic", lambda: 1300.0)
    finish = AsyncMock()
    monkeypatch.setattr(reverse, "_finish_game", finish)
    cb = SimpleNamespace(
        data=f"rcroc_stop_{chat_id}",
        answer=AsyncMock(),
        message=SimpleNamespace(),
    )

    try:
        asyncio.run(reverse.handle_callback(cb))
        cb.answer.assert_awaited_once_with("Слабаки")
        finish.assert_awaited_once()
        assert "КОТ" in finish.await_args.args[1]
    finally:
        reverse.games.pop(chat_id, None)
