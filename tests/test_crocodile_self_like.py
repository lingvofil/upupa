import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_artist_cannot_like_own_attributed_drawing(monkeypatch):
    from games import crocodile_ui_enhancements as ui

    callback = SimpleNamespace(
        data="cr_like_abc123",
        from_user=SimpleNamespace(id=2, full_name="Второй"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-42),
            message_id=777,
        ),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(
        ui.crocodile_ratings,
        "get_like_target",
        lambda token: {
            "chat_id": "-42",
            "artists": [
                {"id": 1, "name": "Первый"},
                {"id": 2, "name": "Второй"},
            ],
        },
    )
    register_like = AsyncMock()
    credit_like = MagicMock(return_value=True)
    monkeypatch.setattr(ui.crocodile_likes, "handle_like_callback", register_like)
    monkeypatch.setattr(ui.crocodile_ratings, "credit_like", credit_like)

    asyncio.run(ui._handle_attributed_like(callback, "abc123"))

    callback.answer.assert_awaited_once_with(
        "Свой рисунок лайкать нельзя 😏",
        show_alert=True,
    )
    register_like.assert_not_awaited()
    credit_like.assert_not_called()
