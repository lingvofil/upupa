import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, User

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)

CHAT_ID = -1001707530786
ROOT = Path(__file__).resolve().parents[1]


def _message(text: str, user_id: int = 555, first_name: str = "Вася") -> Message:
    return Message.model_construct(
        message_id=1,
        date=0,
        chat=Chat.model_construct(id=CHAT_ID, type="supergroup"),
        from_user=User.model_construct(id=user_id, is_bot=False, first_name=first_name),
        text=text,
    )


def _with_regular_session(monkeypatch, word="жираф"):
    from games import crocodile

    monkeypatch.setitem(
        crocodile.game_sessions,
        str(CHAT_ID),
        {"drawer_id": 111, "drawer_ids": [111], "word": word},
    )
    emit = AsyncMock()
    monkeypatch.setattr(crocodile.sio, "emit", emit)
    return emit


def test_guess_is_relayed_to_drawer_room(monkeypatch):
    from features.crocodile_chat_relay import relay_chat_message

    emit = _with_regular_session(monkeypatch)
    assert asyncio.run(relay_chat_message(_message("это собака?"))) is True

    event, payload = emit.await_args.args
    assert event == "chat_guess"
    assert emit.await_args.kwargs["room"] == "m1001707530786"
    assert payload == {"name": "Вася", "text": "это собака?", "correct": False, "close": False}


def test_correct_guess_is_flagged(monkeypatch):
    from features.crocodile_chat_relay import relay_chat_message

    emit = _with_regular_session(monkeypatch)
    asyncio.run(relay_chat_message(_message("жираф")))
    assert emit.await_args.args[1]["correct"] is True


def test_drawer_commands_and_idle_chats_are_not_relayed(monkeypatch):
    from features.crocodile_chat_relay import relay_chat_message
    from games import crocodile

    emit = _with_regular_session(monkeypatch)
    assert asyncio.run(relay_chat_message(_message("подсказка", user_id=111))) is False
    assert asyncio.run(relay_chat_message(_message("/stop"))) is False
    crocodile.game_sessions.pop(str(CHAT_ID))
    assert asyncio.run(relay_chat_message(_message("жираф"))) is False
    emit.assert_not_awaited()


def test_duel_guess_goes_to_every_artist_room(monkeypatch):
    from features.crocodile_chat_relay import relay_chat_message
    from games import crocodile, crocodile_modes

    monkeypatch.setitem(
        crocodile_modes.duel_games,
        str(CHAT_ID),
        {"phase": "drawing", "word": "жираф", "artists": [(111, "A"), (222, "B")]},
    )
    emit = AsyncMock()
    monkeypatch.setattr(crocodile.sio, "emit", emit)

    asyncio.run(relay_chat_message(_message("кот")))
    rooms = [call.kwargs["room"] for call in emit.await_args_list]
    assert rooms == ["m1001707530786_d1", "m1001707530786_d2"]


def test_middleware_never_breaks_message_handling(monkeypatch):
    from features.crocodile_chat_relay import CrocodileChatRelayMiddleware
    from games import crocodile

    _with_regular_session(monkeypatch)
    monkeypatch.setattr(crocodile.sio, "emit", AsyncMock(side_effect=RuntimeError("boom")))
    handler = AsyncMock(return_value="handled")

    result = asyncio.run(CrocodileChatRelayMiddleware()(handler, _message("кот"), {}))
    assert result == "handled"
    handler.assert_awaited_once()


def test_webapp_renders_guesses_as_text_not_html():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'socket.on("chat_guess"' in source
    assert "document.createTextNode(d.text)" in source
    assert "pointer-events: none" in source
