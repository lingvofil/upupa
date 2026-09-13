import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_any_bot_reply as any_reply


class FakeBot:
    def __init__(self, bot_id=999):
        self.id = bot_id


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id=-1001,
        user_id=1,
        text="иду вперёд",
        reply_message_id=50,
        reply_user_id=999,
        reply_is_bot=True,
        bot_id=999,
    ):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, first_name="Алиса")
        self.text = text
        self.caption = None
        self.bot = FakeBot(bot_id)
        self.reply_to_message = (
            SimpleNamespace(
                message_id=reply_message_id,
                from_user=SimpleNamespace(id=reply_user_id, is_bot=reply_is_bot),
            )
            if reply_message_id is not None
            else None
        )


def _session(**overrides):
    values = {
        "mode": "participants",
        "state": "WAITING_ACTION",
        "action_prompt_message_id": 77,
        "action_target_user_ids": [],
        "participants": {"1": {"user_id": 1, "name": "Алиса"}},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_old_upupa_message_is_accepted_during_open_action_window():
    chat_id = -100700
    dnd.dnd_sessions[chat_id] = _session()
    try:
        message = FakeMessage(chat_id=chat_id, reply_message_id=12)
        assert any_reply.is_any_bot_action_reply(message) is True
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_current_prompt_is_left_to_existing_canonical_route():
    chat_id = -100701
    dnd.dnd_sessions[chat_id] = _session()
    try:
        message = FakeMessage(chat_id=chat_id, reply_message_id=77)
        assert any_reply.is_any_bot_action_reply(message) is False
        assert dnd._is_group_action_reply(message) is True
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_reply_must_be_to_this_bot_not_human_or_another_bot():
    chat_id = -100702
    dnd.dnd_sessions[chat_id] = _session()
    try:
        human = FakeMessage(chat_id=chat_id, reply_message_id=12, reply_is_bot=False)
        other_bot = FakeMessage(chat_id=chat_id, reply_message_id=12, reply_user_id=555)
        assert any_reply.is_any_bot_action_reply(human) is False
        assert any_reply.is_any_bot_action_reply(other_bot) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_old_bot_reply_keeps_target_and_command_guards():
    chat_id = -100703
    dnd.dnd_sessions[chat_id] = _session(action_target_user_ids=[2])
    try:
        not_targeted = FakeMessage(chat_id=chat_id, user_id=1, reply_message_id=12)
        assert any_reply.is_any_bot_action_reply(not_targeted) is False

        dnd.dnd_sessions[chat_id].action_target_user_ids = []
        assert any_reply.is_any_bot_action_reply(
            FakeMessage(chat_id=chat_id, text="дальше", reply_message_id=12)
        ) is False
        assert any_reply.is_any_bot_action_reply(
            FakeMessage(chat_id=chat_id, text="упупа что происходит", reply_message_id=12)
        ) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_relaxed_handler_delegates_to_canonical_action_collector(monkeypatch):
    calls = []

    async def fake_handle(message):
        calls.append(message)

    monkeypatch.setattr(dnd, "handle_free_action", fake_handle)
    message = FakeMessage(reply_message_id=12)

    asyncio.run(any_reply.handle_any_bot_action_reply(message))

    assert calls == [message]


def test_route_configuration_is_idempotent():
    registrations = []

    class MessageObserver:
        def register(self, handler, filter_fn):
            registrations.append((handler, filter_fn))

    router = SimpleNamespace(message=MessageObserver())

    any_reply.configure_dnd_any_bot_replies(router)
    any_reply.configure_dnd_any_bot_replies(router)

    assert registrations == [
        (any_reply.handle_any_bot_action_reply, any_reply.is_any_bot_action_reply)
    ]
