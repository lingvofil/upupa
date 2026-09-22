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
        self.answers = []
        self.reply_to_message = (
            SimpleNamespace(
                message_id=reply_message_id,
                from_user=SimpleNamespace(id=reply_user_id, is_bot=reply_is_bot),
            )
            if reply_message_id is not None
            else None
        )

    async def answer(self, text):
        self.answers.append(text)


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


def _backstory_session(**overrides):
    values = {
        "mode": "abstract",
        "state": "WAITING_BACKSTORY",
        "starter_user_id": 1,
        "backstory_prompt_message_id": 88,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _poll_session(**overrides):
    values = {
        "mode": "participants",
        "state": "WAITING_POLL",
        "participants": {
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боб"},
        },
        "current_poll_id": "poll-1",
        "pending_poll": {
            "poll_id": "poll-1",
            "message_id": 123,
            "options": ["Лезть в окно", "Стучать в дверь"],
            "target_user_ids": [],
            "votes": {},
        },
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


def test_song_command_reply_bypasses_dnd_action_routes():
    chat_id = -100710
    dnd.dnd_sessions[chat_id] = _session()
    try:
        old_reply = FakeMessage(chat_id=chat_id, text="песня чат", reply_message_id=12)
        current_reply = FakeMessage(chat_id=chat_id, text="песня чат", reply_message_id=77)
        assert any_reply.is_any_bot_action_reply(old_reply) is False
        assert dnd._is_group_action_reply(current_reply) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_song_command_reply_bypasses_dnd_backstory_and_poll_routes():
    chat_id = -100711
    try:
        dnd.dnd_sessions[chat_id] = _backstory_session()
        old_backstory_reply = FakeMessage(chat_id=chat_id, text="песня чат", reply_message_id=12)
        current_backstory_reply = FakeMessage(chat_id=chat_id, text="песня чат", reply_message_id=88)
        assert any_reply.is_any_bot_backstory_reply(old_backstory_reply) is False
        assert dnd._is_backstory_reply(current_backstory_reply) is False

        dnd.dnd_sessions[chat_id] = _poll_session()
        poll_reply = FakeMessage(chat_id=chat_id, text="песня чат", reply_message_id=12)
        assert any_reply.is_any_bot_poll_reply(poll_reply) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_old_upupa_message_is_accepted_for_custom_backstory():
    chat_id = -100704
    dnd.dnd_sessions[chat_id] = _backstory_session()
    try:
        message = FakeMessage(
            chat_id=chat_id,
            text="Я вырос в цирке и задолжал гусю.",
            reply_message_id=12,
        )
        assert any_reply.is_any_bot_backstory_reply(message) is True
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_current_backstory_prompt_is_left_to_canonical_route():
    chat_id = -100705
    dnd.dnd_sessions[chat_id] = _backstory_session()
    try:
        message = FakeMessage(
            chat_id=chat_id,
            text="Моя предыстория",
            reply_message_id=88,
        )
        assert any_reply.is_any_bot_backstory_reply(message) is False
        assert dnd._is_backstory_reply(message) is True
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_relaxed_backstory_reply_keeps_host_and_bot_guards(monkeypatch):
    chat_id = -100706
    dnd.dnd_sessions[chat_id] = _backstory_session(starter_user_id=2)
    monkeypatch.setattr(dnd, "_user_is_host", lambda _session, _user_id: False)
    try:
        not_host = FakeMessage(chat_id=chat_id, user_id=1, reply_message_id=12)
        human_reply = FakeMessage(
            chat_id=chat_id,
            user_id=1,
            reply_message_id=12,
            reply_is_bot=False,
        )
        assert any_reply.is_any_bot_backstory_reply(not_host) is False
        assert any_reply.is_any_bot_backstory_reply(human_reply) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_poll_reply_to_upupa_is_not_silently_lost():
    chat_id = -100707
    dnd.dnd_sessions[chat_id] = _poll_session()
    try:
        message = FakeMessage(chat_id=chat_id, text="ну и что теперь", reply_message_id=12)
        assert any_reply.is_any_bot_poll_reply(message) is True
        asyncio.run(any_reply.handle_any_bot_poll_reply(message))
        assert message.answers
        assert "голосование" in message.answers[-1].casefold()
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_poll_reply_number_is_recorded_as_vote(monkeypatch):
    chat_id = -100708
    dnd.dnd_sessions[chat_id] = _poll_session()
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    try:
        message = FakeMessage(chat_id=chat_id, text="2", reply_message_id=12)
        asyncio.run(any_reply.handle_any_bot_poll_reply(message))
        assert dnd.dnd_sessions[chat_id].pending_poll["votes"] == {"1": 1}
        assert "Стучать в дверь" in message.answers[-1]
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_poll_reply_still_requires_this_bot():
    chat_id = -100709
    dnd.dnd_sessions[chat_id] = _poll_session()
    try:
        human = FakeMessage(chat_id=chat_id, reply_message_id=12, reply_is_bot=False)
        other_bot = FakeMessage(chat_id=chat_id, reply_message_id=12, reply_user_id=555)
        assert any_reply.is_any_bot_poll_reply(human) is False
        assert any_reply.is_any_bot_poll_reply(other_bot) is False
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


def test_relaxed_backstory_handler_delegates_to_canonical_handler(monkeypatch):
    calls = []

    async def fake_handle(message):
        calls.append(message)

    monkeypatch.setattr(dnd, "handle_backstory", fake_handle)
    message = FakeMessage(text="Своя предыстория", reply_message_id=12)

    asyncio.run(any_reply.handle_any_bot_backstory_reply(message))

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
        (any_reply.handle_any_bot_action_reply, any_reply.is_any_bot_action_reply),
        (any_reply.handle_any_bot_backstory_reply, any_reply.is_any_bot_backstory_reply),
        (any_reply.handle_any_bot_poll_reply, any_reply.is_any_bot_poll_reply),
    ]
