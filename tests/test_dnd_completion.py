import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI.dnd_completion import DndParticipantCompletionMiddleware


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=999)


def _participant_session(chat_id, *, targets=None, pending_actions=None):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="WAITING_ACTION",
        participants={
            "1": {"user_id": 1, "name": "А"},
            "2": {"user_id": 2, "name": "Б"},
            "3": {"user_id": 3, "name": "В"},
            "4": {"user_id": 4, "name": "Г"},
        },
        action_target_user_ids=list(targets or []),
        action_prompt_message_id=777,
        pending_actions=dict(pending_actions or {}),
        action_deadline=123.0,
    )


def test_action_auto_finishes_when_all_four_participants_have_acted(monkeypatch):
    chat_id = -100801
    session = _participant_session(
        chat_id,
        pending_actions={
            "1": {"user_id": 1, "action": "а"},
            "2": {"user_id": 2, "action": "б"},
            "3": {"user_id": 3, "action": "в"},
        },
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(bot, resolved_chat_id, prompt_message_id):
        calls.append((bot, resolved_chat_id, prompt_message_id))

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    bot = FakeBot()
    event = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def handler(_event, _data):
        session.pending_actions["4"] = {"user_id": 4, "action": "г"}
        return "handled"

    try:
        result = asyncio.run(
            DndParticipantCompletionMiddleware()(handler, event, {"bot": bot})
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "handled"
    assert len(session.pending_actions) == 4
    assert calls == [(bot, chat_id, 777)]


def test_targeted_action_finishes_after_all_targets_not_whole_party(monkeypatch):
    chat_id = -100802
    session = _participant_session(
        chat_id,
        targets=[1, 3],
        pending_actions={"1": {"user_id": 1, "action": "а"}},
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(bot, resolved_chat_id, prompt_message_id):
        calls.append((bot, resolved_chat_id, prompt_message_id))

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    bot = FakeBot()
    event = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def handler(_event, _data):
        session.pending_actions["3"] = {"user_id": 3, "action": "в"}

    try:
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [(bot, chat_id, 777)]


def test_action_does_not_finish_until_every_expected_participant_acts(monkeypatch):
    chat_id = -100803
    session = _participant_session(
        chat_id,
        pending_actions={
            "1": {"user_id": 1, "action": "а"},
            "2": {"user_id": 2, "action": "б"},
        },
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(*args):
        calls.append(args)

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    event = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def handler(_event, _data):
        session.pending_actions["3"] = {"user_id": 3, "action": "в"}

    try:
        asyncio.run(
            DndParticipantCompletionMiddleware()(handler, event, {"bot": FakeBot()})
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == []


def test_valid_reply_is_precollected_before_normal_handler(monkeypatch):
    chat_id = -100806
    session = _participant_session(chat_id)
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=4, first_name="Г"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="ломаю шкаф",
        caption=None,
    )

    async def handler(_event, _data):
        assert session.pending_actions["4"]["action"] == "ломаю шкаф"
        return "handled"

    try:
        result = asyncio.run(
            DndParticipantCompletionMiddleware()(handler, event, {"bot": bot})
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "handled"
    assert session.pending_actions["4"] == {
        "user_id": 4,
        "name": "Г",
        "action": "ломаю шкаф",
    }


def test_unregistered_reply_gets_explicit_rejection(monkeypatch):
    chat_id = -100807
    session = _participant_session(chat_id)
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=9, first_name="Лишний"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="я тоже иду",
        caption=None,
    )

    async def handler(_event, _data):
        return None

    try:
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert "9" not in session.pending_actions
    assert bot.messages
    assert "не записан" in bot.messages[-1][1]


def test_non_targeted_participant_gets_explicit_rejection(monkeypatch):
    chat_id = -100808
    session = _participant_session(chat_id, targets=[1, 2, 3])
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=4, first_name="Г"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="а я тоже полез",
        caption=None,
    )

    async def handler(_event, _data):
        return None

    try:
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert "4" not in session.pending_actions
    assert bot.messages
    assert "эта движуха для" in bot.messages[-1][1]


def test_poll_auto_finishes_when_all_eligible_participants_voted(monkeypatch):
    chat_id = -100804
    poll_id = "poll-804"
    session = _participant_session(chat_id)
    session.state = "WAITING_POLL"
    session.current_poll_id = poll_id
    session.pending_poll = {
        "message_id": 888,
        "options": ["лево", "право"],
        "target_user_ids": [],
        "votes": {"1": 0, "2": 1, "3": 0},
    }
    dnd.dnd_sessions[chat_id] = session
    dnd.poll_map[poll_id] = chat_id
    calls = []

    async def fake_finalize(bot, resolved_chat_id, message_id, options):
        calls.append((bot, resolved_chat_id, message_id, options))

    monkeypatch.setattr(dnd, "finalize_poll", fake_finalize)
    bot = FakeBot()
    event = SimpleNamespace(poll_id=poll_id)

    async def handler(_event, _data):
        session.pending_poll["votes"]["4"] = 1

    try:
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.poll_map.pop(poll_id, None)
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [(bot, chat_id, 888, ["лево", "право"])]


def test_abstract_mode_keeps_waiting_for_timer(monkeypatch):
    chat_id = -100805
    session = _participant_session(
        chat_id,
        pending_actions={
            str(user_id): {"user_id": user_id, "action": "x"}
            for user_id in range(1, 5)
        },
    )
    session.mode = "abstract"
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(*args):
        calls.append(args)

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    event = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def handler(_event, _data):
        return None

    try:
        asyncio.run(
            DndParticipantCompletionMiddleware()(handler, event, {"bot": FakeBot()})
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == []
