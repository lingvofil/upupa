import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd


class FakeBot:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeMessage:
    def __init__(self, *, chat_id, text, reply_to_message_id=None):
        self.chat = SimpleNamespace(id=chat_id)
        self.text = text
        self.caption = None
        self.from_user = SimpleNamespace(id=1, first_name="Игрок")
        self.reply_to_message = (
            SimpleNamespace(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        )
        self.bot = FakeBot()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.answers))


def test_backstory_router_only_accepts_reply_to_current_prompt():
    chat_id = -100601
    session = SimpleNamespace(
        state="WAITING_BACKSTORY",
        backstory_prompt_message_id=77,
    )
    dnd.dnd_sessions[chat_id] = session
    try:
        plain = FakeMessage(chat_id=chat_id, text="слегка")
        wrong_reply = FakeMessage(
            chat_id=chat_id,
            text="слегка",
            reply_to_message_id=76,
        )
        correct_reply = FakeMessage(
            chat_id=chat_id,
            text="слегка",
            reply_to_message_id=77,
        )

        assert dnd._is_backstory_reply(plain) is False
        assert dnd._is_backstory_reply(wrong_reply) is False
        assert dnd._is_backstory_reply(correct_reply) is True

        dnd._processing_backstories.add(chat_id)
        try:
            assert dnd._is_backstory_reply(correct_reply) is False
        finally:
            dnd._processing_backstories.discard(chat_id)
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_backstory_is_claimed_in_memory_during_generation(monkeypatch):
    chat_id = -100602
    session = SimpleNamespace(
        state="WAITING_BACKSTORY",
        backstory_prompt_message_id=88,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda _session, prompt: prompt)

    observed_states = []

    async def fake_generate(current_session, _prompt):
        observed_states.append(
            (
                current_session.state,
                current_session.backstory_prompt_message_id,
                chat_id in dnd._processing_backstories,
            )
        )
        return "Сцена [ACTION:INPUT]"

    async def fake_parse(_bot, _chat_id, _text):
        return None

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)

    try:
        message = FakeMessage(
            chat_id=chat_id,
            text="Я проснулся в подвале",
            reply_to_message_id=88,
        )
        asyncio.run(dnd.handle_backstory(message))
        assert observed_states == [("WAITING_BACKSTORY", 88, True)]
        assert session.backstory_prompt_message_id is None
        assert chat_id not in dnd._processing_backstories
    finally:
        dnd._processing_backstories.discard(chat_id)
        dnd.dnd_sessions.pop(chat_id, None)


def test_backstory_failure_reopens_same_prompt(monkeypatch):
    chat_id = -100603
    session = SimpleNamespace(
        state="WAITING_BACKSTORY",
        backstory_prompt_message_id=99,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda _session, prompt: prompt)

    async def fail_generate(_session, _prompt):
        raise RuntimeError("boom")

    monkeypatch.setattr(dnd, "generate_session_response", fail_generate)

    try:
        message = FakeMessage(
            chat_id=chat_id,
            text="Предыстория",
            reply_to_message_id=99,
        )
        asyncio.run(dnd.handle_backstory(message))
        assert session.state == "WAITING_BACKSTORY"
        assert session.backstory_prompt_message_id == 99
        assert chat_id not in dnd._processing_backstories
    finally:
        dnd._processing_backstories.discard(chat_id)
        dnd.dnd_sessions.pop(chat_id, None)
