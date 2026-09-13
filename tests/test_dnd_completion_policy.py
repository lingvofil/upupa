import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI.dnd_completion import DndCompletionPolicy, DndParticipantCompletionMiddleware


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=999)


def _session(chat_id, *, targets=None):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="WAITING_ACTION",
        participants={
            "1": {"user_id": 1, "name": "А"},
            "2": {"user_id": 2, "name": "Б"},
            "3": {"user_id": 3, "name": "В"},
        },
        action_target_user_ids=list(targets or []),
        action_prompt_message_id=777,
        pending_actions={},
        action_deadline=123.0,
    )


def test_policy_filters_expected_ids_per_middleware_instance():
    session = _session(-1009201)
    policy = DndCompletionPolicy()
    policy.filter_expected_ids = lambda _dnd, _session, expected: expected - {2}

    filtered = DndParticipantCompletionMiddleware(policy=policy)
    default = DndParticipantCompletionMiddleware()

    assert filtered._expected_ids(dnd, session, []) == {1, 3}
    assert default._expected_ids(dnd, session, []) == {1, 2, 3}


def test_late_join_hook_runs_after_target_rejection(monkeypatch):
    chat_id = -1009202
    session = _session(chat_id, targets=[1, 2])
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()
    observed = []

    policy = DndCompletionPolicy()

    async def after_join(_dnd, callback_bot, event, callback_session, user_id, user_name):
        observed.append(
            {
                "user_id": user_id,
                "user_name": user_name,
                "joined": str(user_id) in callback_session.participants,
                "pending": str(user_id) in callback_session.pending_actions,
                "message_before_hook": callback_bot.messages[-1][1],
                "event": event,
            }
        )

    policy.after_participant_joined = after_join
    middleware = DndParticipantCompletionMiddleware(policy=policy)
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=9, first_name="Новый"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="вмешиваюсь",
        caption=None,
    )

    async def handler(_event, _data):
        return "handled"

    try:
        result = asyncio.run(middleware(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "handled"
    assert len(observed) == 1
    assert observed[0]["user_id"] == 9
    assert observed[0]["user_name"] == "Новый"
    assert observed[0]["joined"] is True
    assert observed[0]["pending"] is False
    assert "твой ответ не учтён" in observed[0]["message_before_hook"]
    assert observed[0]["event"] is event


def test_late_join_hook_runs_after_open_turn_action_is_precollected(monkeypatch):
    chat_id = -1009203
    session = _session(chat_id)
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()
    observed = []

    policy = DndCompletionPolicy()

    async def after_join(_dnd, _bot, _event, callback_session, user_id, _user_name):
        observed.append(callback_session.pending_actions[str(user_id)]["action"])

    policy.after_participant_joined = after_join
    middleware = DndParticipantCompletionMiddleware(policy=policy)
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=9, first_name="Новый"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="врываюсь в дверь",
        caption=None,
    )

    async def handler(_event, _data):
        return None

    try:
        asyncio.run(middleware(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert observed == ["врываюсь в дверь"]
