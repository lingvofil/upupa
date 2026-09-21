import asyncio
from types import SimpleNamespace

from AI import dnd_result_recovery as recovery
from AI.dnd_group_action_resilience import install_dnd_group_action_resilience


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(
            message_id=100 + len(self.messages),
            chat=SimpleNamespace(id=chat_id),
        )


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
        conversation=[],
        pending_generated_result={},
        pending_generation_request={},
        generated_result_seq=0,
    )


def _fake_dnd(session, generate):
    persisted = []

    async def parse_and_execute_turn(_bot, _chat_id, _text):
        return None

    return SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        persist_dnd_sessions=lambda: persisted.append(True),
        _format_group_actions=lambda actions: "\n".join(
            f"- {item['name']}: {item['action']}" for item in actions
        ),
        with_scene_direction=lambda _session, prompt: prompt,
        generate_session_response=generate,
        parse_and_execute_turn=parse_and_execute_turn,
    ), persisted


def test_group_turn_is_durable_before_provider_call(monkeypatch):
    session = _session()
    seen = []

    async def generate(_session, _prompt):
        raise AssertionError("group wrapper delegates generation to durable recovery")

    async def fake_continue(dnd, bot, current):
        del dnd, bot
        request = current.pending_generation_request
        seen.append(
            {
                "state": current.state,
                "actions": dict(current.pending_actions),
                "prompt_message_id": current.action_prompt_message_id,
                "kind": request.get("kind"),
                "prompt": request.get("prompt"),
                "effects": list(request.get("telegram_effects") or []),
            }
        )
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", fake_continue)

    dnd, persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert seen[0]["state"] == "RESOLVING"
    assert seen[0]["actions"] == {}
    assert seen[0]["prompt_message_id"] is None
    assert seen[0]["kind"] == "GROUP_ACTION_CONTINUATION"
    assert "Алиса: ломаю дверь (id=1)" in seen[0]["prompt"]
    assert "Боря: ищу ловушку (id=2)" in seen[0]["prompt"]
    assert seen[0]["effects"][0]["method"] == "send_message"
    assert seen[0]["effects"][0]["text"].startswith("🎭 Ход партии:")
    assert session.action_target_user_ids == []
    assert session.action_deadline is None
    assert persisted
    assert bot.messages == []


def test_failed_group_generation_keeps_exact_request_for_next_command(monkeypatch):
    session = _session()

    async def generate(_session, _prompt):
        raise AssertionError("not called directly")

    async def fail_continue(_dnd, _bot, _session):
        return False

    monkeypatch.setattr(recovery, "continue_pending_generation", fail_continue)

    dnd, _persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert session.state == "RESOLVING"
    assert session.pending_actions == {}
    assert session.pending_generation_request["kind"] == "GROUP_ACTION_CONTINUATION"
    assert "Алиса: ломаю дверь (id=1)" in session.pending_generation_request["prompt"]
    assert len(bot.messages) == 1
    assert "коллективный ход сохранён" in bot.messages[0][1]
    assert "дальше" in bot.messages[0][1]


def test_retry_does_not_duplicate_group_turn_announcement():
    session = _session()
    attempts = []

    async def fail_generate(_session, prompt):
        attempts.append(prompt)
        raise RuntimeError("providers unavailable")

    dnd, _persisted = _fake_dnd(session, fail_generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    action_announcements = [
        text for _chat_id, text, _kwargs in bot.messages
        if text.startswith("🎭 Ход партии:")
    ]
    assert len(action_announcements) == 1
    assert session.pending_generation_request["telegram_effects"][0]["status"] == recovery.EFFECT_DONE

    # This is what the host's «дальше» command does for a RESOLVING session.
    asyncio.run(recovery.continue_pending_generation(dnd, bot, session))

    action_announcements = [
        text for _chat_id, text, _kwargs in bot.messages
        if text.startswith("🎭 Ход партии:")
    ]
    assert len(action_announcements) == 1
    assert len(attempts) == 2
