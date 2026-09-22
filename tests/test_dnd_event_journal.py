from types import SimpleNamespace

from AI.dnd_event_journal import (
    EVENT_JOURNAL_LIMIT,
    prepare_session_events,
    prospective_revision,
)


def _session():
    return SimpleNamespace(
        chat_id=-1001,
        campaign_started_at="2026-09-22T17:00:00+00:00",
        campaign_id="campaign-test",
        state_revision=0,
        event_journal=[],
        player_positions={"1": {"location": "площадь", "detail": ""}},
        inventories={
            "1": [{"name": "Ключ", "kind": "artifact"}],
            "2": [],
        },
        character_sheets={
            "1": {"hp": 10, "max_hp": 10, "status": "alive"},
            "2": {"hp": 8, "max_hp": 8, "status": "alive"},
        },
        enemy_combatants={},
        npc_memory={},
        conditions={},
        reputations={},
        threat={"name": "Шум", "level": 0, "max": 6},
        scene_clocks={},
    )


def _types(events):
    return [event["type"] for event in events]


def test_first_persist_establishes_baseline_without_synthetic_history():
    session = _session()

    assert prepare_session_events(session) == []
    assert session.state_revision == 0
    assert session.event_journal == []


def test_one_durable_snapshot_commits_multiple_changes_under_one_revision():
    session = _session()
    prepare_session_events(session)

    session.player_positions["1"] = {
        "location": "банка",
        "detail": "сидит внутри",
    }
    session.character_sheets["1"]["hp"] = 4

    events = prepare_session_events(session)

    assert session.state_revision == 1
    assert _types(events) == [
        "PLAYER_POSITION_CHANGED",
        "PLAYER_HP_CHANGED",
    ]
    assert {event["revision"] for event in events} == {1}
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["campaign_id"] == "campaign-test" for event in events)
    assert events[0]["event_id"] == "campaign-test:1:1"
    assert events[1]["event_id"] == "campaign-test:1:2"

    assert prepare_session_events(session) == []
    assert session.state_revision == 1


def test_inventory_move_is_recorded_as_transfer_not_remove_plus_add():
    session = _session()
    prepare_session_events(session)

    item = session.inventories["1"].pop()
    session.inventories["2"].append(item)

    events = prepare_session_events(session)

    assert _types(events) == ["ITEM_TRANSFERRED"]
    assert events[0]["data"] == {
        "name": "Ключ",
        "kind": "artifact",
        "quantity": 1,
        "from_player_id": "1",
        "to_player_id": "2",
    }


def test_hp_and_death_are_separate_structured_events():
    session = _session()
    prepare_session_events(session)

    session.character_sheets["2"]["hp"] = 0
    session.character_sheets["2"]["status"] = "dead"

    events = prepare_session_events(session)

    assert _types(events) == [
        "PLAYER_HP_CHANGED",
        "PLAYER_STATUS_CHANGED",
    ]
    assert events[0]["data"]["before"] == 8
    assert events[0]["data"]["after"] == 0
    assert events[1]["data"]["before"] == "alive"
    assert events[1]["data"]["after"] == "dead"


def test_npc_condition_reputation_threat_and_clock_changes_are_journaled():
    session = _session()
    prepare_session_events(session)

    session.npc_memory["капитан"] = {
        "name": "Капитан",
        "event": "обманули",
        "notes": ["обещали вернуть груз"],
    }
    session.conditions["1"] = [
        {
            "name": "хромает",
            "effect": "MOVE_DISADVANTAGE",
            "scenes_remaining": 2,
        }
    ]
    session.reputations["1"] = ["тот самый придурок с моста"]
    session.threat["level"] = 2
    session.scene_clocks["alarm"] = {
        "name": "Тревога",
        "value": 1,
        "max": 4,
        "status": "active",
    }

    events = prepare_session_events(session)

    assert _types(events) == [
        "NPC_MEMORY_CHANGED",
        "CONDITIONS_CHANGED",
        "REPUTATION_CHANGED",
        "THREAT_CHANGED",
        "SCENE_CLOCK_CHANGED",
    ]


def test_event_journal_is_bounded_but_revision_keeps_growing():
    session = _session()
    prepare_session_events(session)

    for index in range(EVENT_JOURNAL_LIMIT + 5):
        session.player_positions["1"] = {
            "location": f"место {index}",
            "detail": "",
        }
        prepare_session_events(session)

    assert session.state_revision == EVENT_JOURNAL_LIMIT + 5
    assert len(session.event_journal) == EVENT_JOURNAL_LIMIT
    assert session.event_journal[-1]["revision"] == session.state_revision
    assert session.event_journal[0]["revision"] == 6


def test_pre_campaign_bootstrap_changes_do_not_create_world_events():
    session = _session()
    session.campaign_started_at = None

    assert prepare_session_events(session) == []
    session.inventories["1"].append({"name": "Стартовая кружка", "kind": "item"})
    assert prepare_session_events(session) == []

    session.campaign_started_at = "2026-09-22T18:00:00+00:00"
    assert prepare_session_events(session) == []
    assert session.state_revision == 0
    assert session.event_journal == []


def test_open_turn_transaction_batches_intermediate_persists_into_one_revision():
    session = _session()
    prepare_session_events(session)
    session.pending_generated_result = {
        "id": "1:turn",
        "text": "сцена",
        "phase": "APPLYING",
        "transaction_open": True,
    }

    session.player_positions["1"] = {
        "location": "банка",
        "detail": "сидит внутри",
    }
    assert prospective_revision(session) == 1
    assert prepare_session_events(session) == []
    assert session.state_revision == 0

    session.character_sheets["1"]["hp"] = 3
    assert prepare_session_events(session) == []
    assert session.state_revision == 0
    assert session.event_journal == []

    session.pending_generated_result["transaction_open"] = False
    events = prepare_session_events(session)

    assert session.state_revision == 1
    assert _types(events) == [
        "PLAYER_POSITION_CHANGED",
        "PLAYER_HP_CHANGED",
    ]
    assert {event["revision"] for event in events} == {1}


def test_successor_can_target_prospective_revision_before_parent_commit():
    session = _session()
    prepare_session_events(session)
    session.character_sheets["1"]["hp"] = 6

    assert session.state_revision == 0
    assert prospective_revision(session) == 1

    events = prepare_session_events(session)

    assert session.state_revision == 1
    assert _types(events) == ["PLAYER_HP_CHANGED"]


def test_revision_advances_for_canonical_resource_change_not_covered_by_named_events():
    session = _session()
    session.special_move_charges = {"1": 1}
    session.scene_count = 3
    prepare_session_events(session)

    session.special_move_charges["1"] = 0

    assert prospective_revision(session) == 1
    events = prepare_session_events(session)

    assert session.state_revision == 1
    assert _types(events) == ["CANONICAL_FIELDS_CHANGED"]
    assert events[0]["data"]["fields"] == ["special_move_charges"]


def test_scene_counter_alone_still_advances_revision():
    session = _session()
    session.scene_count = 10
    prepare_session_events(session)

    session.scene_count = 11
    events = prepare_session_events(session)

    assert session.state_revision == 1
    assert _types(events) == ["CANONICAL_FIELDS_CHANGED"]
    assert events[0]["data"]["fields"] == ["scene_count"]
