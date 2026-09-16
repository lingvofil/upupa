import asyncio
from types import SimpleNamespace

from AI.dnd_turn_control import _group_turn_context, skip_absent_turn


class FakeBot:
    def __init__(self):
        self.messages = []
        self.stopped_polls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))

    async def stop_poll(self, chat_id, message_id):
        self.stopped_polls.append((chat_id, message_id))
        return SimpleNamespace(options=[])


def _session(state="WAITING_ACTION", targets=None):
    target_ids = list(targets or [1])
    return SimpleNamespace(
        chat_id=-1001,
        state=state,
        participants={
            "1": {"user_id": 1, "name": "Детектор"},
            "2": {"user_id": 2, "name": "Алина"},
        },
        character_sheets={
            "1": {"hp": 10, "status": "alive"},
            "2": {"hp": 10, "status": "alive"},
        },
        spotlight_order=[1, 2],
        spotlight_cursor=0,
        spotlight_individual_streak=1,
        spotlight_decisions_since_poll=2,
        spotlight_last_player=None,
        pending_roll=None,
        current_poll_id=None,
        pending_poll=None,
        action_prompt_message_id=77,
        pending_actions={},
        action_deadline=None,
        action_target_user_ids=target_ids,
    )


def _fake_dnd(session, generated_prompts, parsed_responses, persist_calls):
    async def generate(_session, prompt):
        generated_prompts.append(prompt)
        return "Партия смотрит дальше. [ACTION:INPUT]"

    async def parse(_bot, _chat_id, response):
        parsed_responses.append(response)

    return SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        poll_map={},
        _user_is_host=lambda _session, user_id: int(user_id) == 999,
        _participant_name=lambda _session, user_id: _session.participants[str(user_id)]["name"],
        persist_dnd_sessions=lambda: persist_calls.append(True),
        generate_session_response=generate,
        with_scene_direction=lambda _session, prompt: prompt,
        parse_and_execute_turn=parse,
        open_action_window=lambda *_args, **_kwargs: None,
    )


def test_admin_can_skip_unanswered_targeted_action_and_spotlight_moves_on():
    session = _session()
    generated_prompts = []
    parsed_responses = []
    persist_calls = []
    dnd = _fake_dnd(session, generated_prompts, parsed_responses, persist_calls)
    bot = FakeBot()

    consumed = asyncio.run(skip_absent_turn(dnd, bot, session.chat_id, 999))

    assert consumed is True
    assert session.state == "RESOLVING"
    assert session.action_target_user_ids == []
    assert session.spotlight_cursor == 1
    assert session.spotlight_individual_streak == 1
    assert generated_prompts
    assert "не бросай за него кубик" in generated_prompts[0]
    assert "[ACTION:INPUT] без TARGETS" in generated_prompts[0]
    assert parsed_responses == ["Партия смотрит дальше. [ACTION:INPUT]"]
    assert any("пропущен ведущим" in text for _, text, _ in bot.messages)
    assert persist_calls


def test_admin_can_skip_targeted_roll_without_faking_a_result():
    session = _session(state="WAITING_ROLL")
    session.action_target_user_ids = []
    session.pending_roll = {
        "type": "CHECK",
        "skill": "Ловкость рук",
        "reason": "найти потерянные предметы",
        "dc": 12,
        "mode": "NORMAL",
        "target_user_ids": [1],
    }
    generated_prompts = []
    parsed_responses = []
    dnd = _fake_dnd(session, generated_prompts, parsed_responses, [])

    consumed = asyncio.run(skip_absent_turn(dnd, FakeBot(), session.chat_id, 999))

    assert consumed is True
    assert session.pending_roll is None
    assert session.spotlight_cursor == 1
    assert "не считай пропуск успехом или провалом" in generated_prompts[0]


def test_admin_skip_closes_unanswered_targeted_poll():
    session = _session(state="WAITING_POLL")
    session.action_target_user_ids = []
    session.current_poll_id = "poll-1"
    session.pending_poll = {
        "poll_id": "poll-1",
        "poll_chat_id": session.chat_id,
        "message_id": 55,
        "options": ["А", "Б"],
        "target_user_ids": [1],
        "votes": {},
    }
    generated_prompts = []
    parsed_responses = []
    dnd = _fake_dnd(session, generated_prompts, parsed_responses, [])
    dnd.poll_map["poll-1"] = session.chat_id
    bot = FakeBot()

    consumed = asyncio.run(skip_absent_turn(dnd, bot, session.chat_id, 999))

    assert consumed is True
    assert bot.stopped_polls == [(session.chat_id, 55)]
    assert "poll-1" not in dnd.poll_map
    assert session.pending_poll is None
    assert session.current_poll_id is None


def test_dalshe_does_not_swallow_general_party_turn_or_non_host():
    session = _session(targets=[])
    session.action_target_user_ids = []
    dnd = _fake_dnd(session, [], [], [])

    assert asyncio.run(skip_absent_turn(dnd, FakeBot(), session.chat_id, 999)) is False

    session.action_target_user_ids = [1]
    assert asyncio.run(skip_absent_turn(dnd, FakeBot(), session.chat_id, 123)) is False


def test_group_turn_guidance_increases_after_individual_spotlight():
    session = SimpleNamespace(spotlight_individual_streak=0)
    assert _group_turn_context(session) == ""

    session.spotlight_individual_streak = 1
    assert "слегка предпочти" in _group_turn_context(session)
    assert "[ACTION:INPUT] без TARGETS" in _group_turn_context(session)

    session.spotlight_individual_streak = 2
    assert "два индивидуальных хода подряд" in _group_turn_context(session)
    assert "всей партии" in _group_turn_context(session)
