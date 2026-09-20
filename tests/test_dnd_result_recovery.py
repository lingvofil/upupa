import asyncio
import copy
from types import SimpleNamespace

import pytest

from AI import dnd_campaign as campaign
from AI import dnd_result_recovery as recovery
from AI import dnd_target_mentions as target_mentions


class FakeStatePolicy:
    def __init__(self):
        self.ensure_hooks = []
        self.fields = {}
        self.restore_hooks = []

    def add_ensure_hook(self, hook):
        self.ensure_hooks.append(hook)
        return self

    def add_state_field(self, name, provider):
        self.fields[name] = provider
        return self

    def add_restore_hook(self, hook):
        self.restore_hooks.append(hook)
        return self

    def state(self, session):
        row = {"campaign_marker": getattr(session, "campaign_marker", "before")}
        for name, provider in self.fields.items():
            row[name] = copy.deepcopy(provider(session))
        return row

    def restore(self, session, data):
        row = data if isinstance(data, dict) else {}
        session.campaign_marker = row.get("campaign_marker", "before")
        for hook in self.restore_hooks:
            hook(session, row)


class FakeSession:
    def __init__(self, policy, chat_id=-1001):
        self._policy = policy
        self.chat_id = chat_id
        self.state = "RESOLVING"
        self.last_roll_stat = None
        self.pending_roll = None
        self.current_poll_id = None
        self.pending_poll = None
        self.action_prompt_message_id = None
        self.pending_actions = {}
        self.action_deadline = None
        self.action_target_user_ids = []
        self.recent_scene_types = []
        self.backstory_prompt_message_id = None
        self.mode_prompt_message_id = None
        self.lobby_message_id = None
        self.pending_generated_result = {}
        self.generated_result_seq = 0
        self.campaign_marker = "before"

    def to_record(self):
        return {
            "chat_id": self.chat_id,
            "state": self.state,
            "last_roll_stat": self.last_roll_stat,
            "pending_roll": copy.deepcopy(self.pending_roll),
            "current_poll_id": self.current_poll_id,
            "pending_poll": copy.deepcopy(self.pending_poll),
            "action_prompt_message_id": self.action_prompt_message_id,
            "pending_actions": copy.deepcopy(self.pending_actions),
            "action_deadline": self.action_deadline,
            "action_target_user_ids": list(self.action_target_user_ids),
            "recent_scene_types": list(self.recent_scene_types),
            "backstory_prompt_message_id": self.backstory_prompt_message_id,
            "mode_prompt_message_id": self.mode_prompt_message_id,
            "lobby_message_id": self.lobby_message_id,
            "campaign_state": self._policy.state(self),
        }


def _fake_dnd(policy, *, generated="готовая сцена [ACTION:INPUT]"):
    session = FakeSession(policy)
    calls = {"generate": 0, "parse": 0, "persist": 0, "open": 0, "restore": 0}
    parse_seen_markers = []
    scheduled = []

    async def generate_session_response(_session, _prompt):
        calls["generate"] += 1
        return generated

    async def parse_and_execute_turn(_bot, _chat_id, _text):
        calls["parse"] += 1
        parse_seen_markers.append(session.campaign_marker)
        session.state = "WAITING_ACTION"

    async def open_action_window(_bot, _chat_id, target_user_ids=None):
        calls["open"] += 1
        session.state = "WAITING_ACTION"
        session.action_target_user_ids = list(target_user_ids or [])
        return "opened"

    def restore_dnd_sessions(_bot):
        calls["restore"] += 1
        return 1

    def start_background_task(coro, *, name=None):
        scheduled.append((coro, name))
        return None

    dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy),
        persist_dnd_sessions=lambda: calls.__setitem__("persist", calls["persist"] + 1),
        generate_session_response=generate_session_response,
        parse_and_execute_turn=parse_and_execute_turn,
        open_action_window=open_action_window,
        restore_dnd_sessions=restore_dnd_sessions,
        _start_background_task=start_background_task,
    )
    return dnd, session, calls, parse_seen_markers, scheduled


def test_generation_is_persisted_and_reused_without_second_model_call():
    policy = FakeStatePolicy()
    dnd, session, calls, _, _ = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)

    first = asyncio.run(dnd.generate_session_response(session, "ход один"))
    second = asyncio.run(dnd.generate_session_response(session, "ход два"))

    assert first == second == "готовая сцена [ACTION:INPUT]"
    assert calls["generate"] == 1
    assert session.pending_generated_result["phase"] == recovery.RESULT_PHASE_READY
    assert session.pending_generated_result["text"] == first
    assert session.pending_generated_result["id"].startswith("1:")
    assert calls["persist"] >= 1


def test_ephemeral_generation_bypasses_outbox_and_does_not_overwrite_pending_result():
    policy = FakeStatePolicy()
    dnd, session, calls, _, _ = _fake_dnd(policy, generated="основной ответ")
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)

    assert asyncio.run(dnd.generate_session_response(session, "основной ход")) == "основной ответ"
    pending = copy.deepcopy(session.pending_generated_result)

    session._upupa_ephemeral_generation_depth = 1
    assert asyncio.run(dnd.generate_session_response(session, "служебный запрос")) == "основной ответ"
    del session._upupa_ephemeral_generation_depth

    assert calls["generate"] == 2
    assert session.pending_generated_result == pending


def test_successful_parse_clears_durable_result():
    policy = FakeStatePolicy()
    dnd, session, calls, _, _ = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))

    asyncio.run(dnd.parse_and_execute_turn(None, session.chat_id, response))

    assert calls["parse"] == 1
    assert session.state == "WAITING_ACTION"
    assert session.pending_generated_result == {}


def test_failed_parse_keeps_snapshot_and_replay_restores_preapply_state():
    response = "готовая сцена [ACTION:INPUT]"
    # First parse mutates durable state and then crashes; replay must restore
    # the pre-apply snapshot before applying the exact same response again.
    policy2 = FakeStatePolicy()
    session2 = FakeSession(policy2, chat_id=-1002)
    calls2 = {"generate": 0, "parse": 0, "persist": 0}
    seen2 = []

    async def gen(_session, _prompt):
        calls2["generate"] += 1
        return response

    async def parse(_bot, _chat_id, _text):
        calls2["parse"] += 1
        seen2.append(session2.campaign_marker)
        if calls2["parse"] == 1:
            session2.campaign_marker = "partially-mutated"
            raise RuntimeError("crash during parse")
        session2.state = "WAITING_ACTION"

    dnd2 = SimpleNamespace(
        dnd_sessions={session2.chat_id: session2},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy2),
        persist_dnd_sessions=lambda: calls2.__setitem__("persist", calls2["persist"] + 1),
        generate_session_response=gen,
        parse_and_execute_turn=parse,
        open_action_window=lambda *args, **kwargs: None,
        restore_dnd_sessions=lambda _bot: 1,
        _start_background_task=lambda *args, **kwargs: None,
    )
    recovery.configure_dnd_result_recovery(dnd2, state_policy=policy2)
    result = asyncio.run(dnd2.generate_session_response(session2, "ход"))

    with pytest.raises(RuntimeError):
        asyncio.run(dnd2.parse_and_execute_turn(None, session2.chat_id, result))

    assert session2.pending_generated_result["phase"] == recovery.RESULT_PHASE_APPLYING
    assert session2.pending_generated_result["pre_apply_snapshot"]["campaign_state"]["campaign_marker"] == "before"
    assert session2.campaign_marker == "partially-mutated"

    asyncio.run(recovery._resume_pending_result(dnd2, None, session2, policy2))

    assert calls2["parse"] == 2
    assert seen2 == ["before", "before"]
    assert session2.state == "WAITING_ACTION"
    assert session2.pending_generated_result == {}


def test_ready_group_result_replay_discards_old_collected_actions_before_parse():
    policy = FakeStatePolicy()
    dnd, session, calls, _, _ = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))
    session.pending_actions = {"1": {"user_id": 1, "action": "ломаю дверь"}}
    session.action_prompt_message_id = 77
    session.action_target_user_ids = [1]

    asyncio.run(recovery._resume_pending_result(dnd, None, session, policy))

    assert calls["parse"] == 1
    assert session.pending_actions == {}
    assert session.action_prompt_message_id is None
    assert session.action_target_user_ids == []
    assert session.pending_generated_result == {}
    assert response == "готовая сцена [ACTION:INPUT]"


def test_restore_schedules_exact_ready_result_for_replay():
    policy = FakeStatePolicy()
    dnd, session, calls, _, scheduled = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))

    restored = dnd.restore_dnd_sessions(object())

    assert restored == 1
    assert calls["restore"] == 1
    assert len(scheduled) == 1
    coro, name = scheduled.pop()
    assert "dnd-result-replay" in name
    asyncio.run(coro)
    assert calls["parse"] == 1
    assert session.pending_generated_result == {}
    assert response == "готовая сцена [ACTION:INPUT]"


def test_restore_replays_applying_outbox_even_if_next_state_was_already_committed():
    policy = FakeStatePolicy()
    dnd, session, calls, _, scheduled = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))
    session.pending_generated_result["phase"] = recovery.RESULT_PHASE_APPLYING
    session.pending_generated_result["pre_apply_snapshot"] = recovery._snapshot_parse_state(session)
    session.state = "WAITING_ROLL"
    session.pending_roll = {
        "type": "CHECK",
        "skill": None,
        "reason": "прыгнуть",
        "dc": 12,
        "mode": "NORMAL",
        "target_user_ids": [1],
    }

    dnd.restore_dnd_sessions(object())

    assert len(scheduled) == 1
    coro, _name = scheduled.pop()
    asyncio.run(coro)
    assert calls["parse"] == 1
    assert session.state == "WAITING_ACTION"
    assert session.pending_generated_result == {}
    assert response == "готовая сцена [ACTION:INPUT]"


def test_campaign_ephemeral_generation_sets_suppression_flag():
    observed = []
    session = SimpleNamespace(chat_id=-1003, conversation=[])

    class FakeDnd:
        dnd_sessions = {session.chat_id: session}

        @staticmethod
        async def generate_session_response(current, prompt):
            observed.append(getattr(current, "_upupa_ephemeral_generation_depth", 0))
            current.conversation.append({"role": "user", "content": prompt})
            current.conversation.append({"role": "assistant", "content": "служебный ответ"})
            return "служебный ответ"

        @staticmethod
        def persist_dnd_sessions():
            pass

    result = asyncio.run(campaign._ephemeral_generate(FakeDnd, session, "служебный запрос"))

    assert result == "служебный ответ"
    assert observed == [1]
    assert not hasattr(session, "_upupa_ephemeral_generation_depth")
    assert session.conversation == []


class FakeTelegramBot:
    def __init__(self):
        self.messages = []
        self.polls = []

    async def send_message(self, chat_id, text, **kwargs):
        message_id = 100 + len(self.messages)
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=message_id, chat=SimpleNamespace(id=chat_id))

    async def send_poll(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        if chat_id is None and args:
            chat_id = args[0]
        message_id = 200 + len(self.polls)
        poll_id = f"poll-{message_id}"
        self.polls.append((args, kwargs))
        return SimpleNamespace(
            message_id=message_id,
            chat=SimpleNamespace(id=chat_id),
            poll=SimpleNamespace(id=poll_id),
        )


def test_completed_story_send_is_not_duplicated_when_parse_replays():
    policy = FakeStatePolicy()
    session = FakeSession(policy, chat_id=-1010)
    calls = {"generate": 0, "parse": 0, "persist": 0}
    transport = FakeTelegramBot()

    async def generate(_session, _prompt):
        calls["generate"] += 1
        return "сцена [ACTION:INPUT]"

    async def parse(bot, _chat_id, _text):
        calls["parse"] += 1
        await bot.send_message(session.chat_id, "сцена")
        if calls["parse"] == 1:
            raise RuntimeError("crash after Telegram accepted story")
        session.state = "WAITING_ACTION"

    dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy),
        persist_dnd_sessions=lambda: calls.__setitem__("persist", calls["persist"] + 1),
        generate_session_response=generate,
        parse_and_execute_turn=parse,
        open_action_window=lambda *args, **kwargs: None,
        restore_dnd_sessions=lambda _bot: 1,
        _start_background_task=lambda *args, **kwargs: None,
    )
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))

    with pytest.raises(RuntimeError):
        asyncio.run(dnd.parse_and_execute_turn(transport, session.chat_id, response))

    effects = session.pending_generated_result["telegram_effects"]
    assert len(effects) == 1
    assert effects[0]["status"] == recovery.EFFECT_DONE
    assert len(transport.messages) == 1

    asyncio.run(recovery._resume_pending_result(dnd, transport, session, policy))

    assert calls["parse"] == 2
    assert len(transport.messages) == 1
    assert session.pending_generated_result == {}


def test_completed_poll_send_replays_with_synthetic_original_ids():
    policy = FakeStatePolicy()
    session = FakeSession(policy, chat_id=-1011)
    calls = {"generate": 0, "parse": 0, "persist": 0}
    seen_poll_ids = []
    transport = FakeTelegramBot()

    async def generate(_session, _prompt):
        calls["generate"] += 1
        return "выбор [ACTION:POLL;OPTIONS:А;Б]"

    async def parse(bot, _chat_id, _text):
        calls["parse"] += 1
        poll_msg = await bot.send_poll(
            chat_id=session.chat_id,
            question="Куда?",
            options=["А", "Б"],
            is_anonymous=False,
        )
        seen_poll_ids.append((poll_msg.poll.id, poll_msg.message_id, poll_msg.chat.id))
        if calls["parse"] == 1:
            raise RuntimeError("crash after poll creation")
        session.state = "WAITING_POLL"
        session.current_poll_id = str(poll_msg.poll.id)
        session.pending_poll = {
            "poll_id": str(poll_msg.poll.id),
            "message_id": poll_msg.message_id,
            "poll_chat_id": poll_msg.chat.id,
            "options": ["А", "Б"],
            "target_user_ids": [],
            "votes": {},
        }

    dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        dnd_router=SimpleNamespace(_upupa_dnd_campaign_state_policy=policy),
        persist_dnd_sessions=lambda: calls.__setitem__("persist", calls["persist"] + 1),
        generate_session_response=generate,
        parse_and_execute_turn=parse,
        open_action_window=lambda *args, **kwargs: None,
        restore_dnd_sessions=lambda _bot: 1,
        _start_background_task=lambda *args, **kwargs: None,
    )
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))

    with pytest.raises(RuntimeError):
        asyncio.run(dnd.parse_and_execute_turn(transport, session.chat_id, response))
    assert len(transport.polls) == 1

    asyncio.run(recovery._resume_pending_result(dnd, transport, session, policy))

    assert calls["parse"] == 2
    assert len(transport.polls) == 1
    assert seen_poll_ids == [
        ("poll-200", 200, session.chat_id),
        ("poll-200", 200, session.chat_id),
    ]
    assert session.current_poll_id == "poll-200"
    assert session.pending_generated_result == {}


def test_in_flight_telegram_effect_is_retried_because_delivery_is_ambiguous():
    policy = FakeStatePolicy()
    dnd, session, _calls, _, _ = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    response = asyncio.run(dnd.generate_session_response(session, "ход"))
    session.pending_generated_result["phase"] = recovery.RESULT_PHASE_APPLYING
    session.pending_generated_result["telegram_effects"] = [
        {"index": 0, "method": "send_message", "status": recovery.EFFECT_IN_FLIGHT}
    ]
    transport = FakeTelegramBot()
    proxy = recovery._DurableBotProxy(transport, dnd, session)

    result = asyncio.run(proxy.send_message(session.chat_id, "повтор"))

    assert result.message_id == 100
    assert len(transport.messages) == 1
    assert session.pending_generated_result["telegram_effects"][0]["status"] == recovery.EFFECT_DONE
    assert response == "готовая сцена [ACTION:INPUT]"


def test_target_mentions_unwrap_style_but_keep_durable_transport():
    policy = FakeStatePolicy()
    dnd, session, _calls, _, _ = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(dnd, state_policy=policy)
    transport = FakeTelegramBot()
    durable = recovery._DurableBotProxy(transport, dnd, session)
    styled = SimpleNamespace(_bot=durable)

    resolved = target_mentions._unwrap_bot(styled)

    assert resolved is durable
