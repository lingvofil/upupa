import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _song_message(user_id: int):
    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(send_chat_action=AsyncMock())
    message = SimpleNamespace(
        text="песня чат",
        entities=[],
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=-1001),
        message_id=55,
        bot=bot,
        reply=AsyncMock(return_value=status),
    )
    return message, status


def test_song_command_is_silent_for_non_admin(monkeypatch):
    import handlers.song as song_handler

    build_chat_song = AsyncMock()
    monkeypatch.setattr(song_handler, "ADMIN_ID", 100)
    monkeypatch.setattr(song_handler, "build_chat_song", build_chat_song)

    message, _status = _song_message(user_id=200)
    asyncio.run(song_handler.handle_song_command(message))

    message.reply.assert_not_awaited()
    build_chat_song.assert_not_awaited()
    message.bot.send_chat_action.assert_not_awaited()


def test_song_command_still_runs_for_admin(monkeypatch):
    import handlers.song as song_handler

    generated_song = object()
    build_chat_song = AsyncMock(return_value=generated_song)
    send_song = AsyncMock()
    monkeypatch.setattr(song_handler, "ADMIN_ID", 100)
    monkeypatch.setattr(song_handler, "build_chat_song", build_chat_song)
    monkeypatch.setattr(song_handler, "_send_song", send_song)

    message, status = _song_message(user_id=100)
    asyncio.run(song_handler.handle_song_command(message))

    message.reply.assert_awaited_once()
    build_chat_song.assert_awaited_once_with("-1001")
    send_song.assert_awaited_once_with(message, status, generated_song)
