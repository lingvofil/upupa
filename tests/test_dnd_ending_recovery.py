import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_result_recovery as recovery


class FakeMessage:
    def __init__(self, chat_id, user_id=7):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, first_name="Ведущий")
        self.bot = SimpleNamespace()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def _active_session(chat_id):
    return SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_POLL",
        current_poll_id="poll-ending",
        pending_poll={
            "poll_id": "poll-ending",
            "message_id": 321,
            "poll_chat_id": chat_id,
            "options": ["драться", "бежать"],
            "votes": {"7": 0},
            "target_user_ids": [],
        },
        action_prompt_message_id=555,
        pending_actions={"7": {"user_id": 7, "name": "Ведущий", "action": "драться"}},
        action_deadline=123.0,
        action_target_user_ids=[7],
        pending_roll={"type": "CHECK"},
        pending_generated_result={},
        pending_generation_request={},
        generated_result_seq=0,
        conversation=[],
    )


def test_failed_ending_keeps_session_and_commits_retryable_request(monkeypatch):
    chat_id = -100919
    session = _active_session(chat_id)
    dnd.dnd_sessions[chat_id] = session
    dnd.poll_map["poll-ending"] = chat_id
    persisted = []

    async def fail_continue(_dnd, _bot, current):
        assert current is session
        return False

    monkeypatch.setattr(recovery, "continue_pending_generation", fail_continue)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))

    message = FakeMessage(chat_id)
    try:
        asyncio.run(dnd.cmd_stop_dnd(message))

        assert dnd.dnd_sessions[chat_id] is session
        assert session.state == "RESOLVING"
        assert session.current_poll_id is None
        assert session.pending_poll is None
        assert session.action_prompt_message_id is None
        assert session.pending_actions == {}
        assert session.action_deadline is None
        assert session.action_target_user_ids == []
        assert session.pending_roll is None
        assert "poll-ending" not in dnd.poll_map

        request = session.pending_generation_request
        assert request["kind"] == "ENDING"
        assert request["prompt"].startswith("Игроки хотят конец игры")
        assert request["telegram_effects"] == [
            {
                "method": "stop_poll",
                "chat_id": chat_id,
                "message_id": 321,
                "best_effort": True,
                "index": 0,
                "status": "PENDING",
            }
        ]
        assert persisted
        assert len(message.answers) == 1
        assert "игра сохранена" in message.answers[0][0].casefold()
        assert "дальше" in message.answers[0][0].casefold()
    finally:
        dnd.dnd_sessions.pop(chat_id, None)
        dnd.poll_map.pop("poll-ending", None)


def test_ending_does_not_replace_an_unrelated_pending_generation(monkeypatch):
    chat_id = -100920
    session = _active_session(chat_id)
    session.state = "RESOLVING"
    session.current_poll_id = None
    session.pending_poll = None
    session.pending_generation_request = {"prompt": "старое незавершённое продолжение"}
    dnd.dnd_sessions[chat_id] = session

    called = []

    async def must_not_continue(*args, **kwargs):
        called.append((args, kwargs))
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", must_not_continue)

    message = FakeMessage(chat_id)
    try:
        asyncio.run(dnd.cmd_stop_dnd(message))

        assert session.pending_generation_request["prompt"] == "старое незавершённое продолжение"
        assert called == []
        assert len(message.answers) == 1
        assert "сначала напиши «дальше»" in message.answers[0][0].casefold()
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_ending_does_not_discard_a_pending_generated_result(monkeypatch):
    chat_id = -100921
    session = _active_session(chat_id)
    session.state = "RESOLVING"
    session.current_poll_id = None
    session.pending_poll = None
    session.pending_generated_result = {
        "id": "42:existing",
        "text": "Уже сгенерированная сцена [ACTION:INPUT]",
        "phase": "READY",
        "telegram_effects": [],
    }
    dnd.dnd_sessions[chat_id] = session

    called = []

    async def must_not_continue(*args, **kwargs):
        called.append((args, kwargs))
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", must_not_continue)

    message = FakeMessage(chat_id)
    try:
        asyncio.run(dnd.cmd_stop_dnd(message))

        assert session.pending_generated_result["id"] == "42:existing"
        assert session.pending_generated_result["text"].startswith("Уже сгенерированная сцена")
        assert session.pending_generation_request == {}
        assert called == []
        assert len(message.answers) == 1
        assert "сначала напиши «дальше»" in message.answers[0][0].casefold()
    finally:
        dnd.dnd_sessions.pop(chat_id, None)
