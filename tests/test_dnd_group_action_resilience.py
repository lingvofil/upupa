import asyncio
from types import SimpleNamespace

from AI.dnd_group_action_resilience import install_dnd_group_action_resilience


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


def _session(chat_id=-1001):
    return SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ACTION",
        action_prompt_message_id=77,
        pending_actions={
            "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
            "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
        },
        action_target_user_ids=[1, 2],
        action_deadline=123.0,
    )


def _fake_dnd(session, generate):
    parsed = []
    opened = []
    persisted = []

    async def parse_and_execute_turn(bot, chat_id, text):
        parsed.append((bot, chat_id, text))

    async def open_action_window(bot, chat_id):
        opened.append((bot, chat_id))

    dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        persist_dnd_sessions=lambda: persisted.append(True),
        _format_group_actions=lambda actions: "\n".join(
            f"- {item['name']}: {item['action']}" for item in actions
        ),
        with_scene_direction=lambda _session, prompt: prompt,
        generate_session_response=generate,
        parse_and_execute_turn=parse_and_execute_turn,
        open_action_window=open_action_window,
    )
    return dnd, parsed, opened, persisted


def test_generation_failure_preserves_collected_group_actions():
    session = _session()

    async def fail_generate(_session, _prompt):
        raise RuntimeError("all providers unavailable")

    dnd, parsed, opened, persisted = _fake_dnd(session, fail_generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert session.state == "WAITING_ACTION"
    assert session.action_prompt_message_id == 77
    assert session.pending_actions == {
        "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
        "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
    }
    assert session.action_target_user_ids == [1, 2]
    assert session.action_deadline is None
    assert parsed == []
    assert opened == []
    assert persisted
    assert "Ход сохранён" in bot.messages[-1][1]


def test_success_consumes_actions_only_after_generation():
    session = _session()
    state_seen_during_generation = []

    async def generate(current_session, _prompt):
        state_seen_during_generation.append(
            (
                current_session.state,
                dict(current_session.pending_actions),
                current_session.action_prompt_message_id,
            )
        )
        return "сцена [ACTION:INPUT]"

    dnd, parsed, opened, persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert state_seen_during_generation[0][0] == "RESOLVING"
    assert len(state_seen_during_generation[0][1]) == 2
    assert state_seen_during_generation[0][2] == 77
    assert session.pending_actions == {}
    assert session.action_prompt_message_id is None
    assert session.action_target_user_ids == []
    assert parsed == [(bot, session.chat_id, "сцена [ACTION:INPUT]")]
    assert opened == []
    assert persisted
