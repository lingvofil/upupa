import asyncio
from types import SimpleNamespace

from AI.dnd_group_action_resilience import install_dnd_group_action_resilience
from AI.dnd_spotlight import enforce_spotlight


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


def _spotlight_session():
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Алина"},
            "2": {"user_id": 2, "name": "M&M"},
        },
        character_sheets={
            "1": {"hp": 10, "status": "alive"},
            "2": {"hp": 10, "status": "alive"},
        },
        spotlight_order=[1, 2],
        spotlight_cursor=1,
        spotlight_individual_streak=0,
        spotlight_decisions_since_poll=0,
        spotlight_last_player=1,
    )


def test_group_resolution_roll_is_not_reassigned_to_next_spotlight_player():
    session = _spotlight_session()
    session._upupa_resolving_group_actions = True
    response = (
        "Алина тянется к жетону. "
        "[ACTION:ROLL;TYPE:CHECK;TARGETS:1;SKILL:Скрытность;REASON:стащить жетон;DC:11;MODE:NORMAL]"
    )

    guarded, consumed, rewritten = enforce_spotlight(session, response)

    assert guarded == response
    assert "TARGETS:1" in guarded
    assert "TARGETS:2" not in guarded
    assert consumed is None
    assert rewritten is False
    assert session.spotlight_cursor == 1


def test_group_resolution_prompt_binds_roll_to_actor_and_marks_parse_context():
    chat_id = -1001
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ACTION",
        action_prompt_message_id=77,
        pending_actions={
            "1": {"user_id": 1, "name": "Алина", "action": "незаметно стащить жетон"},
            "2": {"user_id": 2, "name": "M&M", "action": "завести тарантас"},
        },
        action_target_user_ids=[1, 2],
        action_deadline=123.0,
    )
    prompts = []
    parse_context_flags = []

    async def generate(_session, prompt):
        prompts.append(prompt)
        return (
            "Алина тянется к жетону. "
            "[ACTION:ROLL;TYPE:CHECK;TARGETS:1;SKILL:Скрытность;REASON:стащить жетон;DC:11;MODE:NORMAL]"
        )

    async def parse_and_execute_turn(_bot, _chat_id, _text):
        parse_context_flags.append(bool(getattr(session, "_upupa_resolving_group_actions", False)))

    dnd = SimpleNamespace(
        dnd_sessions={chat_id: session},
        persist_dnd_sessions=lambda: None,
        _format_group_actions=lambda actions: "\n".join(
            f"- {item['name']}: {item['action']}" for item in actions
        ),
        with_scene_direction=lambda _session, prompt: prompt,
        generate_session_response=generate,
        parse_and_execute_turn=parse_and_execute_turn,
        open_action_window=lambda *_args, **_kwargs: None,
    )
    install_dnd_group_action_resilience(dnd)

    asyncio.run(dnd.finalize_group_actions(FakeBot(), chat_id, 77))

    assert len(prompts) == 1
    prompt = prompts[0]
    assert "Алина (id=1): незаметно стащить жетон" in prompt
    assert "M&M (id=2): завести тарантас" in prompt
    assert "не предрешай его исход" in prompt
    assert "TARGETS этого броска обязан содержать id именно того игрока" in prompt
    assert "не выдавай и не отнимай" in prompt
    assert parse_context_flags == [True]
    assert not hasattr(session, "_upupa_resolving_group_actions")
