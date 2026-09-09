import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI.dnd_completion import (
    DND_PARTICIPANT_CONTEXT_MARKER,
    DndParticipantCompletionMiddleware,
    _refresh_gemini_chat_session,
    _with_participant_context,
)


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


def test_unregistered_reply_joins_open_group_turn(monkeypatch):
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
        assert dnd._can_user_act(session, 9, []) is True
        return "handled"

    try:
        result = asyncio.run(
            DndParticipantCompletionMiddleware()(handler, event, {"bot": bot})
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "handled"
    assert session.participants["9"] == {"user_id": 9, "name": "Лишний"}
    assert session.pending_actions["9"] == {
        "user_id": 9,
        "name": "Лишний",
        "action": "я тоже иду",
    }
    assert bot.messages == []


def test_late_joiner_counts_for_open_turn_auto_finalize(monkeypatch):
    chat_id = -100809
    session = _participant_session(
        chat_id,
        pending_actions={
            str(user_id): {"user_id": user_id, "action": "x"}
            for user_id in range(1, 5)
        },
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    calls = []

    async def fake_finalize(bot, resolved_chat_id, prompt_message_id):
        calls.append((bot, resolved_chat_id, prompt_message_id))

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    bot = FakeBot()
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
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert set(dnd._participant_ids(session)) == {1, 2, 3, 4, 9}
    assert "9" in session.pending_actions
    assert calls == [(bot, chat_id, 777)]


def test_unregistered_reply_joins_but_waits_on_targeted_turn(monkeypatch):
    chat_id = -100810
    session = _participant_session(chat_id, targets=[1, 2])
    dnd.dnd_sessions[chat_id] = session
    persisted = []
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    bot = FakeBot()
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=9, first_name="Лишний"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="вмешиваюсь",
        caption=None,
    )

    async def handler(_event, _data):
        assert dnd._can_user_act(session, 9, [1, 2]) is False
        return None

    try:
        asyncio.run(DndParticipantCompletionMiddleware()(handler, event, {"bot": bot}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.participants["9"] == {"user_id": 9, "name": "Лишний"}
    assert "9" not in session.pending_actions
    assert persisted == [True]
    assert bot.messages
    assert "ты влез в егру" in bot.messages[-1][1]
    assert "щас ход А, Б" in bot.messages[-1][1]
    assert "твой ответ не учтён" in bot.messages[-1][1]


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


def test_live_participant_context_contains_late_joiner_id():
    session = _participant_session(-100811)
    session.participants["9"] = {"user_id": 9, "name": "Новый"}

    prompt = _with_participant_context(dnd, session, "Продолжай сцену")

    assert DND_PARTICIPANT_CONTEXT_MARKER in prompt
    assert "- ID 9: Новый" in prompt
    assert "актуальный состав" in prompt
    assert "TARGETS" in prompt


def test_abstract_mode_does_not_get_participant_context():
    session = _participant_session(-100812)
    session.mode = "abstract"

    assert _with_participant_context(dnd, session, "продолжай") == "продолжай"


def test_gemini_chat_session_rebuild_drops_invalid_sdk_role():
    starts = []
    fresh_chat = object()

    def fake_start_chat(*, chat_id, history):
        starts.append((chat_id, history))
        return fresh_chat

    fake_dnd = SimpleNamespace(
        model=SimpleNamespace(start_chat=fake_start_chat),
    )
    poisoned_chat = object()
    session = SimpleNamespace(
        chat_id=-100813,
        active_model="gemini",
        conversation=[
            {"role": "user", "content": "первый ход"},
            {"role": "assistant", "content": "ответ мастера"},
            {"role": None, "content": "сломанная SDK-запись"},
        ],
        chat_session=poisoned_chat,
    )

    _refresh_gemini_chat_session(fake_dnd, session)

    assert session.chat_session is fresh_chat
    assert starts == [
        (
            -100813,
            [
                {"role": "user", "parts": ["первый ход"]},
                {"role": "model", "parts": ["ответ мастера"]},
            ],
        )
    ]


def test_non_gemini_chat_session_is_not_rebuilt():
    starts = []
    original_chat = object()
    fake_dnd = SimpleNamespace(
        model=SimpleNamespace(start_chat=lambda **kwargs: starts.append(kwargs)),
    )
    session = SimpleNamespace(
        chat_id=-100814,
        active_model="groq",
        conversation=[{"role": "user", "content": "ход"}],
        chat_session=original_chat,
    )

    _refresh_gemini_chat_session(fake_dnd, session)

    assert session.chat_session is original_chat
    assert starts == []


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