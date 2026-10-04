import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests import test_smoke_imports  # noqa: F401

import features.interactive_settings as interactive_settings
from core.settings import ADMIN_ID
from core.state import chat_settings
from handlers.basic import handle_left_chat_member


def _run(coro):
    return asyncio.run(coro)


def _button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


@pytest.fixture(autouse=True)
def restore_chat_settings():
    saved = deepcopy(chat_settings)
    yield
    chat_settings.clear()
    chat_settings.update(saved)


async def _world_disabled(_chat_id):
    return False


def _member(*, name="Иван Иванов", is_bot=False, username=None, user_id=123):
    return SimpleNamespace(
        full_name=name,
        is_bot=is_bot,
        username=username,
        id=user_id,
    )


def _message(chat_id=-1001, member=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        left_chat_member=member or _member(),
        answer=AsyncMock(),
    )


def test_leave_notifications_setting_is_off_by_default(monkeypatch):
    chat_settings.clear()
    monkeypatch.setattr(interactive_settings, "is_world_enabled", _world_disabled)

    text, markup = _run(interactive_settings.get_main_settings_markup("-1001"))

    assert "🚪 *Уведомления об уходе:* Выкл. ❌" in text
    assert "Вкл. уходы" in _button_texts(markup)


def test_leave_notifications_setting_shows_disable_action_when_enabled(monkeypatch):
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    monkeypatch.setattr(interactive_settings, "is_world_enabled", _world_disabled)

    text, markup = _run(interactive_settings.get_main_settings_markup("-1001"))

    assert "🚪 *Уведомления об уходе:* Вкл. ✅" in text
    assert "Выкл. уходы" in _button_texts(markup)


def test_leave_notification_is_silent_by_default():
    chat_settings.clear()
    message = _message()

    _run(handle_left_chat_member(message))

    message.answer.assert_not_awaited()


def test_leave_notification_uses_member_full_name_when_enabled():
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    message = _message(member=_member(name="Иван Иванов"))

    _run(handle_left_chat_member(message))

    message.answer.assert_awaited_once_with(
        "этот пидорас Иван Иванов только что убежал"
    )


def test_leave_notification_ignores_bots():
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    message = _message(member=_member(name="Другой бот", is_bot=True))

    _run(handle_left_chat_member(message))

    message.answer.assert_not_awaited()


def test_leave_notifications_toggle_defaults_to_enable(monkeypatch):
    chat_settings.clear()
    save_mock = Mock()
    monkeypatch.setattr(interactive_settings, "save_chat_settings", save_mock)
    monkeypatch.setattr(
        interactive_settings,
        "get_main_settings_markup",
        AsyncMock(return_value=("settings", None)),
    )

    query = SimpleNamespace(
        data="settings:toggle:leave_notifications",
        from_user=SimpleNamespace(id=ADMIN_ID),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type="supergroup"),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    _run(interactive_settings.handle_settings_callback(query))

    assert chat_settings["-1001"]["leave_notifications_enabled"] is True
    save_mock.assert_called_once_with()
    query.answer.assert_awaited_once_with("Настройка сохранена")
