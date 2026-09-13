from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from features.chronicle.models import ChronicleEvent
from features.social_graph.relationship_history import assemble_relationship_history
from features.social_graph.relationships import RelationshipSnapshot, RelationshipView


def _event(*, when: datetime, source: str = "live", title: str = "Великая война за кондиционер") -> ChronicleEvent:
    return ChronicleEvent(
        id=f"event-{source}-{int(when.timestamp())}",
        chat_id=-1001707530786,
        created_at=when,
        event_started_at=when,
        event_ended_at=when + timedelta(minutes=20),
        title=title,
        summary="Пара устроила заметный эпизод, который сохранила Летопись.",
        category="conflict" if source == "live" else "game",
        importance_score=9.0,
        participant_ids=[1, 2],
        participant_names=["Alice", "Bob"],
        participant_usernames=["alice", "bob"],
        source_message_ids=[123],
        anchor_message_id=123,
        reaction_count=4,
        unique_reactors=3,
        reply_count=8,
        keywords=[],
        entities=[],
        related_event_ids=[],
        ai_metadata={},
        source=source,
    )


def _view(events=()) -> RelationshipView:
    now = datetime(2026, 5, 5, tzinfo=timezone.utc)
    return RelationshipView(
        chat_id=-1001707530786,
        user_a_id=1,
        user_b_id=2,
        user_a_name="Alice",
        user_b_name="Bob",
        affinity=68,
        tension=54,
        xp=45.0,
        level=9,
        reciprocity=0.82,
        archetype="заклятые друзья",
        trend="конфликт набирает обороты",
        interaction_count=50,
        reply_count=30,
        mention_count=10,
        reaction_count=10,
        shared_event_count=len(events),
        a_to_b_count=26,
        b_to_a_count=24,
        recent_7d=12,
        previous_21d=18,
        last_interaction_at=now,
        shared_events=tuple(events),
    )


def _snapshot(when: datetime, *, level: int, affinity: int, tension: int, archetype: str) -> RelationshipSnapshot:
    return RelationshipSnapshot(
        captured_at=when,
        affinity=affinity,
        tension=tension,
        xp=float(level * 4),
        level=level,
        reciprocity=0.8,
        archetype=archetype,
        trend="отношения стабильны",
    )


def test_chronicle_wins_over_nearby_relationship_snapshot():
    april_1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
    april_18 = datetime(2026, 4, 18, 12, tzinfo=timezone.utc)
    event = _event(when=april_18)
    history = assemble_relationship_history(
        _view((event,)),
        (
            _snapshot(april_1, level=5, affinity=45, tension=10, archetype="регулярные собеседники"),
            _snapshot(april_18 + timedelta(hours=2), level=7, affinity=58, tension=52, archetype="спорщики"),
        ),
    )

    chronicle_items = [item for item in history.timeline if item.chronicle_event is not None]
    assert len(chronicle_items) == 1
    assert chronicle_items[0].title == "Великая война за кондиционер"
    assert all(item.event_type != "archetype_change" for item in history.timeline)


def test_significant_snapshot_transition_is_kept_without_chronicle_duplicate():
    april_1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
    may_5 = datetime(2026, 5, 5, tzinfo=timezone.utc)
    history = assemble_relationship_history(
        _view(),
        (
            _snapshot(april_1, level=4, affinity=35, tension=10, archetype="периодически пересекаются"),
            _snapshot(may_5, level=8, affinity=67, tension=12, archetype="стабильный дуэт"),
        ),
    )

    transition = next(item for item in history.timeline if item.event_type == "archetype_change")
    assert "уровень: 4/15 → 8/15" in transition.summary
    assert "близость заметно выросла" in transition.summary


def test_external_game_events_are_reused_through_chronicle_source():
    when = datetime(2026, 5, 10, tzinfo=timezone.utc)
    event = _event(when=when, source="dnd", title="Провал в подземелье")
    history = assemble_relationship_history(_view((event,)), ())

    assert len(history.timeline) == 1
    assert history.timeline[0].source == "chronicle:dnd"
    assert history.timeline[0].event_type == "chronicle_game"


def test_relationship_history_ai_receives_only_precomputed_facts():
    from features.social_graph.ai import narrate_relationship_history

    when = datetime(2026, 5, 10, tzinfo=timezone.utc)
    event = _event(when=when)
    history = assemble_relationship_history(_view((event,)), ())
    prompts = []

    async def generator(prompt: str, chat_id: str) -> str:
        prompts.append((prompt, chat_id))
        return "Сначала спорили, потом спорить не перестали — зато всё это уже официально в Летописи."

    result = asyncio.run(
        narrate_relationship_history(history.view, history.timeline, "-1001707530786", generator=generator)
    )

    assert result is not None
    assert prompts
    prompt, chat_id = prompts[0]
    assert chat_id == "-1001707530786"
    assert "Великая война за кондиционер" in prompt
    assert "Это НЕ анализ переписки" in prompt


def test_pair_resolver_supports_one_user_two_users_and_reply(monkeypatch):
    import handlers.social_graph as handler

    async def resolve(_chat_id, usernames):
        known = {
            "bob": (2, "Bob", "bob"),
            "carol": (3, "Carol", "carol"),
        }
        return {name: known[name] for name in usernames if name in known}

    monkeypatch.setattr(handler, "resolve_relationship_usernames", resolve)
    actor = SimpleNamespace(id=1, is_bot=False)

    def message(text, reply_user=None):
        replied = SimpleNamespace(from_user=reply_user) if reply_user is not None else None
        return SimpleNamespace(
            text=text,
            chat=SimpleNamespace(id=-1001707530786),
            from_user=actor,
            reply_to_message=replied,
        )

    assert asyncio.run(handler._resolve_relationship_pair(message("история @bob"), "история")) == (1, 2)
    assert asyncio.run(handler._resolve_relationship_pair(message("история @bob @carol"), "история")) == (2, 3)
    assert asyncio.run(
        handler._resolve_relationship_pair(
            message("история", SimpleNamespace(id=3, is_bot=False)),
            "история",
        )
    ) == (1, 3)
