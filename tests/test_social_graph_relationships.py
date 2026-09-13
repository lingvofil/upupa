from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.chronicle.models import ChronicleEvent
from features.social_graph import relationships
from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository


def _event(*, category: str, participants=(1, 2), importance: float = 8.0) -> ChronicleEvent:
    now = datetime.now(timezone.utc)
    return ChronicleEvent(
        id=f"event-{category}",
        chat_id=-100,
        created_at=now,
        event_started_at=now - timedelta(hours=1),
        event_ended_at=now,
        title="Событие",
        summary="Зафиксированный контекст",
        category=category,
        importance_score=importance,
        participant_ids=list(participants),
        participant_names=[f"User {item}" for item in participants],
        participant_usernames=[None for _ in participants],
        source_message_ids=[10],
        anchor_message_id=10,
        reaction_count=0,
        unique_reactors=0,
        reply_count=1,
        keywords=[],
        entities=[],
        related_event_ids=[],
        ai_metadata={},
        source="live",
    )


def test_relationship_state_is_durable_and_directional(tmp_path):
    repo = SQLiteSocialGraphRepository(tmp_path / "stats.db")
    repo.init_schema()
    now = datetime.now(timezone.utc)

    inserted = repo.record_message_bundle(
        -100,
        1,
        now,
        (2, "B", "b"),
        ((1, "A", "a"), (2, "B", "b")),
        ((1, "reply", 3.0),),
    )
    assert inserted == 1
    assert repo.record_interaction(-100, (1, "A", "a"), 2, "reaction", 2, now, 1.0)

    rows = repo.load_relationship_states(-100)
    assert len(rows) == 1
    row = rows[0]
    assert (row["user_a_id"], row["user_b_id"]) == (1, 2)
    assert row["interaction_count"] == 2
    assert row["reply_count"] == 1
    assert row["reaction_count"] == 1
    assert row["a_to_b_count"] == 1
    assert row["b_to_a_count"] == 1
    assert 1.0 < row["xp"] < 1.5  # second same-day signal is diminished


@pytest.mark.asyncio
async def test_tension_comes_from_grounded_conflict_events_not_interaction_volume(monkeypatch):
    row = {
        "user_a_id": 1,
        "user_b_id": 2,
        "user_a_name": "A",
        "user_b_name": "B",
        "xp": 40.0,
        "interaction_count": 40,
        "reply_count": 30,
        "mention_count": 5,
        "reaction_count": 5,
        "a_to_b_count": 20,
        "b_to_a_count": 20,
        "a_to_b_weight": 50.0,
        "b_to_a_weight": 50.0,
        "recent_7d": 8,
        "previous_21d": 18,
        "last_interaction_at": datetime.now(timezone.utc).isoformat(),
    }

    async def fake_rows(chat_id, *, user_id=None):
        return [row]

    async def no_events(chat_id, *, limit):
        return []

    async def no_snapshot(view):
        return None

    monkeypatch.setattr(relationships, "load_relationship_states", fake_rows)
    monkeypatch.setattr(relationships, "list_chronicle_events", no_events)
    monkeypatch.setattr(relationships, "save_relationship_snapshot", no_snapshot)

    plain = (await relationships.get_relationships(-100))[0]
    assert plain.affinity > 60
    assert plain.tension == 0

    async def conflict_events(chat_id, *, limit):
        return [_event(category="conflict")]

    monkeypatch.setattr(relationships, "list_chronicle_events", conflict_events)
    conflict = (await relationships.get_relationships(-100))[0]
    assert conflict.tension > 0
    assert conflict.shared_event_count == 1


@pytest.mark.asyncio
async def test_chronicle_can_restore_pair_even_without_retained_raw_interactions(monkeypatch):
    async def no_rows(chat_id, *, user_id=None):
        return []

    async def old_event(chat_id, *, limit):
        return [_event(category="local_meme", participants=(10, 20), importance=10.0)]

    async def no_snapshot(view):
        return None

    monkeypatch.setattr(relationships, "load_relationship_states", no_rows)
    monkeypatch.setattr(relationships, "list_chronicle_events", old_event)
    monkeypatch.setattr(relationships, "save_relationship_snapshot", no_snapshot)

    views = await relationships.get_relationships(-100)
    assert len(views) == 1
    assert (views[0].user_a_id, views[0].user_b_id) == (10, 20)
    assert views[0].shared_event_count == 1
    assert views[0].xp > 0
