from pathlib import Path
from types import SimpleNamespace

from AI.dnd_state_invariants import validate_session_state


def _session(**overrides):
    base = {
        "chat_id": -1001,
        "mode": "participants",
        "state": "WAITING_ACTION",
        "participants": {
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        "pending_roll": None,
        "current_poll_id": None,
        "pending_poll": None,
        "pending_heal_decision": None,
        "action_prompt_message_id": None,
        "pending_actions": {},
        "action_target_user_ids": [],
        "character_sheets": {
            "1": {"hp": 8, "max_hp": 10, "status": "alive"},
            "2": {"hp": 0, "max_hp": 12, "status": "dead"},
        },
        "player_positions": {
            "1": {"location": "мост"},
            "2": {"location": "берег"},
        },
        "conditions": {},
        "inventories": {
            "1": [{"name": "Верёвка", "kind": "item"}],
            "2": [{"name": "Ключ порта", "kind": "artifact"}],
        },
        "pending_generation_request": {},
        "pending_generated_result": {},
        "campaign_id": "campaign-test",
        "state_revision": 0,
        "event_journal": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _codes(session):
    return {issue.code for issue in validate_session_state(session)}


def test_valid_waiting_action_state_has_no_invariant_issues():
    assert validate_session_state(_session()) == []


def test_validator_detects_target_and_character_state_contradictions():
    session = _session(
        action_target_user_ids=[999],
        pending_roll={
            "type": "CHECK",
            "target_user_ids": [999],
        },
        state="WAITING_ROLL",
        character_sheets={
            "1": {"hp": 0, "max_hp": 10, "status": "alive"},
            "2": {"hp": 4, "max_hp": 12, "status": "dead"},
        },
    )

    codes = _codes(session)

    assert "invalid_targets" in codes
    assert "alive_without_hp" in codes
    assert "terminal_status_with_hp" in codes


def test_validator_detects_poll_state_without_durable_poll():
    session = _session(state="WAITING_POLL")

    codes = _codes(session)

    assert "waiting_poll_without_poll" in codes
    assert "waiting_poll_without_id" in codes


def test_validator_detects_duplicate_unique_artifact_across_players():
    session = _session(
        inventories={
            "1": [{"name": "Синий ключ", "kind": "artifact"}],
            "2": [{"name": "  СИНИЙ   КЛЮЧ ", "kind": "artifact"}],
        }
    )

    assert "duplicate_unique_artifact" in _codes(session)


def test_validator_detects_overlapping_generation_request_and_result():
    session = _session(
        state="RESOLVING",
        pending_generation_request={"id": "gen:1", "prompt": "продолжай"},
        pending_generated_result={
            "id": "1:abc",
            "text": "готово",
            "phase": "READY",
        },
    )

    assert "generation_request_and_result_overlap" in _codes(session)


def test_applying_result_requires_pre_apply_snapshot():
    session = _session(
        state="RESOLVING",
        pending_generated_result={
            "id": "1:abc",
            "text": "готово",
            "phase": "APPLYING",
        },
    )

    assert "applying_result_without_snapshot" in _codes(session)


def test_player_scoped_state_must_belong_to_current_participants():
    session = _session(
        player_positions={"999": {"location": "крыша"}},
        conditions={"999": [{"name": "хромает"}]},
    )

    codes = _codes(session)

    assert "orphan_player_position" in codes
    assert "orphan_condition_owner" in codes


def test_dnd_validates_state_before_persist_and_after_restore():
    source = (Path(__file__).resolve().parents[1] / "AI" / "dnd.py").read_text(encoding="utf-8")

    persist_block = source.split("def persist_dnd_sessions()", 1)[1].split(
        "def choose_next_scene_type", 1
    )[0]
    restore_block = source.split("def restore_dnd_sessions", 1)[1].split(
        "async def create_game_session", 1
    )[0]

    assert '_validate_dnd_session_state(session, boundary="persist")' in persist_block
    assert '_validate_dnd_session_state(session, boundary="restore")' in restore_block


def test_validator_detects_foreign_or_future_event_journal_entries():
    session = _session(
        state_revision=1,
        event_journal=[
            {
                "event_id": "other:2:1",
                "campaign_id": "other",
                "revision": 2,
                "sequence": 1,
                "type": "PLAYER_HP_CHANGED",
                "data": {},
            }
        ],
    )

    codes = _codes(session)

    assert "foreign_event_campaign" in codes
    assert "event_ahead_of_state" in codes
    assert "journal_revision_mismatch" in codes


def test_validator_detects_stale_generation_request_revision():
    session = _session(
        state="RESOLVING",
        state_revision=3,
        pending_generation_request={
            "id": "gen:stale",
            "prompt": "старый ход",
            "source_campaign_id": "campaign-test",
            "source_revision": 2,
        },
    )

    assert "generation_request_revision_mismatch" in _codes(session)


def test_validator_accepts_applying_result_in_commit_crash_window():
    session = _session(
        state="RESOLVING",
        state_revision=4,
        pending_generated_result={
            "id": "4:apply",
            "text": "готово",
            "phase": "APPLYING",
            "source_campaign_id": "campaign-test",
            "source_revision": 3,
            "transaction_open": False,
            "pre_apply_snapshot": {},
        },
    )

    codes = _codes(session)

    assert "generated_result_revision_mismatch" not in codes
    assert "turn_transaction_phase_mismatch" not in codes


def test_validator_requires_snapshot_for_open_turn_transaction():
    session = _session(
        state="RESOLVING",
        pending_generated_result={
            "id": "1:apply",
            "text": "готово",
            "phase": "APPLYING",
            "source_campaign_id": "campaign-test",
            "source_revision": 0,
            "transaction_open": True,
        },
    )

    assert "turn_transaction_without_snapshot" in _codes(session)
