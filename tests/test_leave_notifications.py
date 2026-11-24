import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests import test_smoke_imports  # noqa: F401

import features.interactive_settings as interactive_settings
import handlers.basic as basic
from core.settings import ADMIN_ID
from core.state import chat_settings
from handlers.basic import handle_chat_member_update, handle_left_chat_member


def _run(coro):
    return asyncio.run(coro)


def _button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


@pytest.fixture(autouse=True)
def restore_chat_settings():
    saved = deepcopy(chat_settings)
    basic._recent_leave_notifications.clear()
    yield
    basic._recent_leave_notifications.clear()
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


def _message(chat_id=-1001, member=None, *, actor=None):
    member = member or _member()
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        left_chat_member=member,
        from_user=actor or member,
        answer=AsyncMock(),
    )


def _chat_member_state(status, member, *, is_member=True):
    return SimpleNamespace(
        status=status,
        user=member,
        is_member=is_member,
    )


def _chat_member_update(
    *,
    chat_id=-1001,
    member=None,
    actor=None,
    old_status="member",
    new_status="left",
    old_is_member=True,
):
    member = member or _member()
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=actor or member,
        old_chat_member=_chat_member_state(
            old_status,
            member,
            is_member=old_is_member,
        ),
        new_chat_member=_chat_member_state(new_status, member),
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


@pytest.mark.parametrize("actor", [None, _member(name="Админ", user_id=321)])
def test_leave_notification_is_silent_by_default(actor):
    chat_settings.clear()
    message = _message(actor=actor)

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


def test_chat_member_update_sends_leave_notification_in_large_chat_path(monkeypatch):
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(basic, "bot", fake_bot)
    update = _chat_member_update(member=_member(name="Большой Чат"))

    _run(handle_chat_member_update(update))

    fake_bot.send_message.assert_awaited_once_with(
        -1001,
        "этот пидорас Большой Чат только что убежал",
    )


@pytest.mark.parametrize("new_status", ["kicked", "left"])
def test_chat_member_update_names_admin_who_removed_member(monkeypatch, new_status):
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(basic, "bot", fake_bot)
    update = _chat_member_update(new_status=new_status, actor=_member(name="Админ", user_id=321))

    _run(handle_chat_member_update(update))

    fake_bot.send_message.assert_awaited_once_with(-1001, "Админ пидорнул Иван Иванов")


@pytest.mark.parametrize("old_status, new_status", [
    ("restricted", "left"),
    ("left", "kicked"),
    ("kicked", "left"),
])
def test_chat_member_update_ignores_already_absent_member(monkeypatch, old_status, new_status):
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(basic, "bot", fake_bot)
    update = _chat_member_update(
        old_status=old_status,
        new_status=new_status,
        old_is_member=False,
        actor=_member(name="Админ", user_id=321),
    )

    _run(handle_chat_member_update(update))

    fake_bot.send_message.assert_not_awaited()


def test_leave_notification_is_deduplicated_between_update_types(monkeypatch):
    chat_settings.clear()
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    member = _member(name="Дубль")
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(basic, "bot", fake_bot)

    _run(handle_chat_member_update(_chat_member_update(member=member)))
    message = _message(member=member)
    _run(handle_left_chat_member(message))

    fake_bot.send_message.assert_awaited_once()
    message.answer.assert_not_awaited()


@pytest.mark.parametrize("service_message_first", [True, False])
def test_kick_is_reported_once_in_either_update_order(monkeypatch, service_message_first):
    chat_settings["-1001"] = {"leave_notifications_enabled": True}
    actor = _member(name="Админ", user_id=321)
    member = _member()
    message = _message(member=member, actor=actor)
    update = _chat_member_update(member=member, actor=actor, new_status="kicked")
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(basic, "bot", fake_bot)
    text = "Админ пидорнул Иван Иванов"

    if service_message_first:
        _run(handle_left_chat_member(message))
        _run(handle_chat_member_update(update))
        message.answer.assert_awaited_once_with(text)
        fake_bot.send_message.assert_not_awaited()
    else:
        _run(handle_chat_member_update(update))
        _run(handle_left_chat_member(message))
        fake_bot.send_message.assert_awaited_once_with(-1001, text)
        message.answer.assert_not_awaited()


def test_basic_router_subscribes_to_chat_member_updates():
    assert "chat_member" in basic.router.resolve_used_update_types()
