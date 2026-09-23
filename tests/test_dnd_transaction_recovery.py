import asyncio
import json
from types import SimpleNamespace

from AI import dnd
from AI import dnd_special_moves as special


def _special_session():
    pending = {
        "type": "CHECK",
        "skill": "Атлетика",
        "reason": "сломать решётку",
        "dc": 12,
        "mode": "ADVANTAGE",
        "target_user_ids": [1],
    }
    session = SimpleNamespace(
        state="WAITING_ROLL",
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        character_profiles={"1": {"special": "аварийный рывок"}},
        special_move_charges={"1": 1},
        special_move_pending_user_id=1,
        special_move_offer_user_id=None,
        pending_roll=pending,
    )
    return session, pending


def test_pending_roll_restore_preserves_extension_payloads():
    session = dnd.GameSession.from_record(
        {
            "chat_id": -100801,
            "active_model": "groq",
            "conversation": [{"role": "assistant", "content": "контекст"}],
            "state": "WAITING_ROLL",
            "pending_roll": {
                "type": "CINEMATIC_ATTACK",
                "skill": None,
                "reason": "уронить люстру",
                "dc": 16,
                "mode": "DISADVANTAGE",
                "target_user_ids": [1],
                "ability": "STR",
                "risk": "HIGH",
                "cinematic": {
                    "enemy_key": "огр",
                    "enemy_name": "Огр",
                    "ability": "STR",
                    "method": "уронить люстру",
                    "tactical_scene_object_id": "chandelier",
                },
                "condition_uses_pending": [
                    {"player": "1", "effect": "COMBAT_DISADVANTAGE"}
                ],
                "weakness_luck_reward": {
                    "player": 1,
                    "complication": "полез под люстру первым",
                    "mode": "DISADVANTAGE",
                },
            },
        }
    )

    assert session.pending_roll["type"] == "CINEMATIC_ATTACK"
    assert session.pending_roll["cinematic"]["enemy_key"] == "огр"
    assert session.pending_roll["cinematic"]["tactical_scene_object_id"] == "chandelier"
    assert session.pending_roll["condition_uses_pending"] == [
        {"player": "1", "effect": "COMBAT_DISADVANTAGE"}
    ]
    assert session.pending_roll["weakness_luck_reward"]["player"] == 1
    assert session.pending_roll["ability"] == "STR"
    assert session.pending_roll["risk"] == "HIGH"


def test_pending_attack_restore_keeps_combat_target_payload():
    session = dnd.GameSession.from_record(
        {
            "chat_id": -100802,
            "active_model": "groq",
            "conversation": [{"role": "assistant", "content": "контекст"}],
            "state": "WAITING_ROLL",
            "pending_roll": {
                "type": "ATTACK",
                "reason": "ударить огра",
                "dc": 13,
                "mode": "NORMAL",
                "target_user_ids": [1],
                "ability": "STR",
                "attack": {
                    "enemy_key": "огр",
                    "enemy_name": "Огр",
                    "weapon": "стул",
                    "style": "MELEE",
                },
            },
        }
    )

    assert session.pending_roll["attack"] == {
        "enemy_key": "огр",
        "enemy_name": "Огр",
        "weapon": "стул",
        "style": "MELEE",
    }


def test_restore_keeps_collected_group_turn_if_process_died_while_resolving(tmp_path, monkeypatch):
    path = tmp_path / "dnd_state.json"
    payload = {
        "version": 1,
        "sessions": [
            {
                "chat_id": -100803,
                "active_model": "groq",
                "conversation": [{"role": "assistant", "content": "контекст"}],
                "mode": "participants",
                "state": "RESOLVING",
                "participants": {
                    "1": {"user_id": 1, "name": "Алиса"},
                    "2": {"user_id": 2, "name": "Боря"},
                },
                "action_prompt_message_id": 77,
                "pending_actions": {
                    "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
                    "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
                },
                "action_deadline": None,
                "action_target_user_ids": [1, 2],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(dnd, "_state_path", lambda: path)

    try:
        restored = dnd.restore_dnd_sessions(SimpleNamespace())
        session = dnd.dnd_sessions[-100803]

        assert restored == 1
        assert session.state == "WAITING_ACTION"
        assert session.action_prompt_message_id == 77
        assert session.pending_actions == payload["sessions"][0]["pending_actions"]
        assert session.action_target_user_ids == [1, 2]
        assert session.action_deadline is None
    finally:
        dnd.dnd_sessions.pop(-100803, None)


def test_restore_without_collected_group_turn_still_falls_back_to_fresh_action(tmp_path, monkeypatch):
    path = tmp_path / "dnd_state.json"
    payload = {
        "version": 1,
        "sessions": [
            {
                "chat_id": -100804,
                "active_model": "groq",
                "conversation": [{"role": "assistant", "content": "контекст"}],
                "mode": "participants",
                "state": "RESOLVING",
                "participants": {"1": {"user_id": 1, "name": "Алиса"}},
                "action_prompt_message_id": 77,
                "pending_actions": {},
                "action_deadline": None,
                "action_target_user_ids": [1],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(dnd, "_state_path", lambda: path)
    scheduled = []
    monkeypatch.setattr(
        dnd,
        "_start_background_task",
        lambda coroutine, **kwargs: (scheduled.append(kwargs), coroutine.close()),
    )

    try:
        restored = dnd.restore_dnd_sessions(SimpleNamespace())
        session = dnd.dnd_sessions[-100804]

        assert restored == 1
        assert session.state == "WAITING_ACTION"
        assert session.action_prompt_message_id is None
        assert session.pending_actions == {}
        assert scheduled
    finally:
        dnd.dnd_sessions.pop(-100804, None)


def test_special_charge_commits_after_actual_roll_even_if_story_continuation_will_fail():
    session, pending = _special_session()
    session.pending_roll = None

    changed = special._commit_completed_special_roll(session, pending, 1)

    assert changed is True
    assert session.special_move_charges["1"] == 0
    assert session.special_move_pending_user_id is None


def test_special_charge_is_not_spent_if_roll_did_not_happen():
    session, pending = _special_session()

    changed = special._commit_completed_special_roll(session, pending, 1)

    assert changed is False
    assert session.special_move_charges["1"] == 1
    assert session.special_move_pending_user_id == 1


def test_special_charge_is_not_spent_by_wrong_actor():
    session, pending = _special_session()
    session.pending_roll = None

    changed = special._commit_completed_special_roll(session, pending, 2)

    assert changed is False
    assert session.special_move_charges["1"] == 1


def test_base_restore_defers_poll_tasks_to_durable_recovery_layer(tmp_path, monkeypatch):
    path = tmp_path / "dnd_state.json"
    path.write_text('{"version": 1, "sessions": [{}]}', encoding="utf-8")
    session = SimpleNamespace(
        chat_id=-100805,
        state="WAITING_POLL",
        current_poll_id="poll-existing",
        pending_poll={
            "poll_id": "poll-existing",
            "poll_chat_id": -100805,
            "message_id": 501,
            "options": ["А", "Б"],
            "deadline": 9999999999.0,
            "target_user_ids": [],
            "votes": {},
        },
        pending_generated_result={"text": "готовый ответ", "phase": "APPLYING"},
        pending_actions={},
        action_prompt_message_id=None,
        action_deadline=None,
        pending_roll=None,
        mode_prompt_message_id=None,
        lobby_message_id=None,
        backstory_prompt_message_id=None,
    )
    scheduled = []

    monkeypatch.setattr(dnd, "_state_path", lambda: path)
    monkeypatch.setattr(
        dnd.GameSession,
        "from_record",
        classmethod(lambda cls, record: session),
    )
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(
        dnd,
        "_start_background_task",
        lambda coroutine, **kwargs: (scheduled.append(kwargs), coroutine.close()),
    )

    try:
        restored = dnd.restore_dnd_sessions(SimpleNamespace())

        assert restored == 1
        # This test imports the uncomposed engine. Its job is to defer the poll;
        # configure_dnd_result_recovery owns replay scheduling in production.
        # The composed restart path is exercised by test_dnd_adventure_composition.
        assert scheduled == []
        assert "poll-existing" not in dnd.poll_map
        assert session.state == "WAITING_POLL"
        assert session.current_poll_id == "poll-existing"
    finally:
        dnd.dnd_sessions.pop(-100805, None)
        dnd.poll_map.pop("poll-existing", None)



def test_conversation_rewind_removes_half_committed_generation_history():
    session = SimpleNamespace(
        active_model="groq",
        chat_id=-100806,
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
            {"role": "user", "content": "roll outcome"},
            {"role": "assistant", "content": "response that was never outboxed"},
        ],
        chat_session=None,
    )

    changed = dnd._rewind_session_conversation(session, 2)

    assert changed is True
    assert session.conversation == [
        {"role": "user", "content": "system"},
        {"role": "assistant", "content": "ready"},
    ]


def test_base_roll_reserves_exact_continuation_before_provider_call(monkeypatch):
    chat_id = -100807
    session = SimpleNamespace(
        chat_id=chat_id,
        mode="abstract",
        state="WAITING_ROLL",
        pending_roll={
            "type": "CHECK",
            "skill": "Атлетика",
            "reason": "перепрыгнуть яму",
            "dc": 12,
            "mode": "NORMAL",
            "target_user_ids": [],
        },
        pending_generation_request={},
        pending_generated_result={},
        conversation=[],
    )
    answers = []
    provider_seen = []
    opened = []

    class Bot:
        def __init__(self):
            self.messages = []

        async def send_message(self, resolved_chat_id, text, **kwargs):
            del kwargs
            self.messages.append((resolved_chat_id, text))
            return SimpleNamespace(
                message_id=900 + len(self.messages),
                chat=SimpleNamespace(id=resolved_chat_id),
            )

    class Message:
        chat = SimpleNamespace(id=chat_id)
        from_user = SimpleNamespace(id=1, first_name="Алиса")

        def __init__(self):
            self.bot = Bot()

        async def answer(self, text, **kwargs):
            del kwargs
            answers.append(text)
            return SimpleNamespace()

    async def fail_generation(current, prompt):
        provider_seen.append(
            {
                "prompt": prompt,
                "request": dict(current.pending_generation_request),
                "state": current.state,
                "pending_roll": current.pending_roll,
            }
        )
        raise RuntimeError("provider unavailable")

    async def open_action(*args, **kwargs):
        opened.append((args, kwargs))

    monkeypatch.setattr(dnd, "_roll_d20", lambda mode: ([17], 17))
    monkeypatch.setattr(dnd, "with_scene_direction", lambda session, prompt: prompt)
    monkeypatch.setattr(dnd, "generate_session_response", fail_generation)
    monkeypatch.setattr(dnd, "open_action_window", open_action)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    dnd.dnd_sessions[chat_id] = session

    try:
        import asyncio

        message = Message()
        asyncio.run(dnd.handle_roll(message))

        assert len(provider_seen) == 1
        snapshot = provider_seen[0]
        assert snapshot["state"] == "RESOLVING"
        assert snapshot["pending_roll"] is None
        assert snapshot["request"]["kind"] == "ROLL_CONTINUATION"
        assert snapshot["request"]["prompt"] == snapshot["prompt"]
        assert "Броски d20: [17]; итог: 17." in snapshot["prompt"]
        assert "Сложность: 12; результат: успех." in snapshot["prompt"]
        effects = session.pending_generation_request["telegram_effects"]
        assert effects[0]["method"] == "send_message"
        assert effects[0]["status"] == "DONE"
        assert "🎲 Алиса: Атлетика — перепрыгнуть яму" in effects[0]["text"]
        assert len(message.bot.messages) == 1
        assert opened == []
        assert any("нового кубика" in text for text in answers)
    finally:
        dnd.dnd_sessions.pop(chat_id, None)



def test_poll_outcome_is_committed_before_continuation_provider(monkeypatch):
    chat_id = -100808
    session = SimpleNamespace(
        chat_id=chat_id,
        mode="abstract",
        state="WAITING_POLL",
        current_poll_id="poll-808",
        pending_poll={
            "poll_id": "poll-808",
            "message_id": 808,
            "poll_chat_id": chat_id,
            "options": ["лево", "право"],
            "target_user_ids": [],
            "votes": {"1": 1, "2": 1},
        },
        pending_generated_result={},
        pending_generation_request={},
        generated_result_seq=0,
        conversation=[],
    )
    provider_seen = []
    opened = []
    sent = []
    stopped = []

    class Bot:
        async def stop_poll(self, *, chat_id, message_id):
            stopped.append((chat_id, message_id))
            return SimpleNamespace(options=[])

        async def send_message(self, chat_id, text, **kwargs):
            del kwargs
            sent.append((chat_id, text))
            return SimpleNamespace(message_id=900 + len(sent), chat=SimpleNamespace(id=chat_id))

    async def fail_generation(current, prompt):
        provider_seen.append(
            {
                "prompt": prompt,
                "request": dict(current.pending_generation_request),
                "state": current.state,
                "poll": current.pending_poll,
            }
        )
        raise RuntimeError("provider unavailable")

    async def open_action(*args, **kwargs):
        opened.append((args, kwargs))

    monkeypatch.setattr(dnd, "generate_session_response", fail_generation)
    monkeypatch.setattr(dnd, "open_action_window", open_action)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda session, prompt: prompt)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    dnd.dnd_sessions[chat_id] = session
    dnd.poll_map["poll-808"] = chat_id

    try:
        asyncio.run(dnd.finalize_poll(Bot(), chat_id, 808, ["лево", "право"]))

        assert session.state == "RESOLVING"
        assert session.current_poll_id is None
        assert session.pending_poll is None
        assert "poll-808" not in dnd.poll_map
        assert len(provider_seen) == 1
        snapshot = provider_seen[0]
        assert snapshot["state"] == "RESOLVING"
        assert snapshot["poll"] is None
        assert snapshot["request"]["kind"] == "POLL_CONTINUATION"
        assert snapshot["request"]["prompt"] == snapshot["prompt"]
        assert "право" in snapshot["prompt"]
        assert "лево" not in snapshot["prompt"]
        assert stopped == [(chat_id, 808)]
        assert sent[0][0] == chat_id
        assert sent[0][1].startswith("✅ ")
        assert "право" in sent[0][1]
        assert "переголосовывать не надо" in sent[-1][1]
        assert opened == []
        assert all(
            effect["status"] == "DONE"
            for effect in session.pending_generation_request["telegram_effects"]
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)
        dnd.poll_map.pop("poll-808", None)
        dnd._finalizing_polls.discard("poll-808")
