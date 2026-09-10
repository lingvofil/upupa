import asyncio
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_telephone_turn_mentions_current_player_and_escapes_name(monkeypatch):
    from games import crocodile_telephone_mentions as mentions

    send_message = AsyncMock()
    monkeypatch.setattr(mentions.crocodile_modes.bot, "send_message", send_message)
    monkeypatch.setattr(mentions.crocodile_modes, "_blank", lambda: b"blank")
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "_canvas_button",
        lambda chat_id, suffix, text: (chat_id, suffix, text),
    )

    game = {
        "step": 1,
        "players": [(101, "Первый"), (202, "М&M <artist>")],
        "chain": [{"kind": "text", "value": "крокодил"}],
    }

    asyncio.run(mentions.send_telephone_step_with_mention("-42", game))

    send_message.assert_awaited_once()
    args, kwargs = send_message.await_args
    assert args[0] == -42
    assert '<a href="tg://user?id=202">М&amp;M &lt;artist&gt;</a>' in args[1]
    assert ": рисуй. Остальные не подглядывают." in args[1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] == ("-42", "t1", "✍️ Открыть свой ход")

    session = mentions.crocodile_modes.canvas_sessions["-42:t1"]
    try:
        assert session["drawer_id"] == 202
        assert session["drawer_name"] == "М&M <artist>"
        assert session["word"] == "крокодил"
        assert session["ui_mode"] == "draw"
    finally:
        mentions.crocodile_modes.canvas_sessions.pop("-42:t1", None)


def test_telephone_first_turn_mentions_player_too(monkeypatch):
    from games import crocodile_telephone_mentions as mentions

    send_message = AsyncMock()
    monkeypatch.setattr(mentions.crocodile_modes.bot, "send_message", send_message)
    monkeypatch.setattr(mentions.crocodile_modes, "_blank", lambda: b"blank")
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "_canvas_button",
        lambda *_args: None,
    )

    game = {
        "step": 0,
        "players": [(303, "Загадыватель")],
        "chain": [],
    }

    try:
        asyncio.run(mentions.send_telephone_step_with_mention("-43", game))
        text = send_message.await_args.args[1]
        assert '<a href="tg://user?id=303">Загадыватель</a>' in text
        assert ": загадывай. Остальные не подглядывают." in text
    finally:
        mentions.crocodile_modes.canvas_sessions.pop("-43:t0", None)
