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


def test_restore_defers_waiting_poll_tasks_while_durable_result_is_pending(tmp_path, monkeypatch):
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
        assert scheduled == []
        assert "poll-existing" not in dnd.poll_map
        assert session.state == "WAITING_POLL"
        assert session.current_poll_id == "poll-existing"
    finally:
        dnd.dnd_sessions.pop(-100805, None)
        dnd.poll_map.pop("poll-existing", None)
