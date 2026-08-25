import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Настраивает fake env и моки тяжёлых библиотек до импорта handler.
from tests import test_smoke_imports  # noqa: F401

import handlers.sms as sms_handler
from core.state import chat_list, sms_disabled_chats


@pytest.fixture(autouse=True)
def restore_sms_state():
    saved_chats = deepcopy(chat_list)
    saved_disabled = set(sms_disabled_chats)
    yield
    chat_list.clear()
    chat_list.extend(saved_chats)
    sms_disabled_chats.clear()
    sms_disabled_chats.update(saved_disabled)


def _message(command: str):
    return SimpleNamespace(
        text=command,
        caption=None,
        chat=SimpleNamespace(id=-1001, title="Исходный чат"),
        reply=AsyncMock(),
    )


def _set_chats():
    chat_list.clear()
    chat_list.extend(
        [
            {"id": -1001, "title": "Исходный чат", "username": None},
            {"id": -1002, "title": "Целевой чат", "username": None},
        ]
    )
    sms_disabled_chats.clear()


def test_sms_to_channel_is_rejected(monkeypatch):
    _set_chats()
    message = _message("смс 2 привет")
    fake_bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(type="channel")))
    send_sms = AsyncMock()
    monkeypatch.setattr(sms_handler, "bot", fake_bot)
    monkeypatch.setattr(sms_handler, "process_send_sms", send_sms)

    asyncio.run(sms_handler.handle_send_sms(message))

    fake_bot.get_chat.assert_awaited_once_with(-1002)
    send_sms.assert_not_awaited()
    message.reply.assert_awaited_once_with("В каналы СМС и ММС не отправляю.")


def test_mms_to_channel_is_rejected(monkeypatch):
    _set_chats()
    message = _message("ммс 2")
    fake_bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(type="channel")))
    send_mms = AsyncMock()
    monkeypatch.setattr(sms_handler, "bot", fake_bot)
    monkeypatch.setattr(sms_handler, "process_send_mms", send_mms)

    asyncio.run(sms_handler.handle_send_mms(message))

    fake_bot.get_chat.assert_awaited_once_with(-1002)
    send_mms.assert_not_awaited()
    message.reply.assert_awaited_once_with("В каналы СМС и ММС не отправляю.")


def test_sms_to_supergroup_still_uses_existing_pipeline(monkeypatch):
    _set_chats()
    message = _message("смс 2 привет")
    fake_bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")))
    send_sms = AsyncMock()
    monkeypatch.setattr(sms_handler, "bot", fake_bot)
    monkeypatch.setattr(sms_handler, "process_send_sms", send_sms)

    asyncio.run(sms_handler.handle_send_sms(message))

    send_sms.assert_awaited_once_with(message, chat_list, fake_bot)
    message.reply.assert_not_awaited()


def test_sms_is_not_sent_when_target_type_cannot_be_verified(monkeypatch):
    _set_chats()
    message = _message("смс 2 привет")
    fake_bot = SimpleNamespace(get_chat=AsyncMock(side_effect=RuntimeError("telegram unavailable")))
    send_sms = AsyncMock()
    monkeypatch.setattr(sms_handler, "bot", fake_bot)
    monkeypatch.setattr(sms_handler, "process_send_sms", send_sms)

    asyncio.run(sms_handler.handle_send_sms(message))

    send_sms.assert_not_awaited()
    message.reply.assert_awaited_once_with(
        "Не могу проверить тип адресата, поэтому СМС/ММС не отправляю."
    )
