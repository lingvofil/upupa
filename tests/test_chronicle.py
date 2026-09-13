import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401


def user(user_id, name=None, username=None, *, is_bot=False):
    return SimpleNamespace(
        id=user_id,
        full_name=name or f"User {user_id}",
        first_name=name or f"User {user_id}",
        username=username,
        is_bot=is_bot,
    )


def message(*, actor, message_id=10, text="hello", reply=None, chat_id=-1001234567890):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="supergroup", username=None, title="Test"),
        from_user=actor,
        message_id=message_id,
        text=text,
        caption=None,
        reply_to_message=reply,
        date=datetime.now(timezone.utc),
    )


def reply_message(*, actor, message_id=5, text="anchor", reply=None):
    return SimpleNamespace(
        from_user=actor,
        message_id=message_id,
        text=text,
        caption=None,
        reply_to_message=reply,
    )


def build_stores(tmp_path):
    from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
    from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore
    from infrastructure.persistence.sqlite_chronicle_events import SQLiteChronicleEventStore

    history = tmp_path / "history.db"
    statistics = tmp_path / "statistics.db"
    candidates = SQLiteChronicleCandidateStore(history, statistics)
    events = SQLiteChronicleEventStore(history)
    backfill = SQLiteChronicleBackfillStore(history)
    candidates.init_schema()
    events.init_schema()
    backfill.init_schema()
    return candidates, events, backfill


def configure_service(monkeypatch, tmp_path):
    import features.chronicle.service as service

    candidates, events, backfill = build_stores(tmp_path)
    service.configure_chronicle(candidates, events, backfill)
    service._backfill_requested.clear()
    monkeypatch.setattr(service.config, "CHRONICLE_ENABLED", True)
    monkeypatch.setattr(service.config, "CHRONICLE_BACKFILL_ENABLED", False)
    return service, candidates, events, backfill


def reaction_update(*, actor, message_id=10, count=1, chat_id=-1001234567890):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="supergroup"),
        user=actor,
        message_id=message_id,
        old_reaction=[],
        new_reaction=[SimpleNamespace(emoji="😂", custom_emoji_id=None, type="emoji") for _ in range(count)],
        date=datetime.now(timezone.utc),
    )


def accepted_decision(participants, *, title="Великая война за чайник", summary="Боб снова воевал с чайником и проиграл."):
    from features.chronicle.ai import ChronicleDecision
    from features.chronicle.models import ChronicleEventDraft

    return ChronicleDecision(
        True,
        ChronicleEventDraft(
            title=title,
            summary=summary,
            category="local_meme",
            confidence=0.9,
            participant_ids=list(participants),
            keywords=["чайник"],
            entities=["чайник"],
            ai_metadata={"reason": "memorable"},
        ),
        "accepted",
        0.9,
    )


def test_plain_message_without_activity_is_not_candidate(monkeypatch, tmp_path):
    service, candidates, _events, _backfill = configure_service(monkeypatch, tmp_path)

    result = asyncio.run(service.capture_message(message(actor=user(1, "Alice"), text="обычный привет")))

    assert result is None
    assert candidates.due_candidates(datetime.now(timezone.utc) + timedelta(hours=1)) == []


def test_multiple_replies_merge_into_one_candidate_and_raise_score(monkeypatch, tmp_path):
    service, candidates, _events, _backfill = configure_service(monkeypatch, tmp_path)
    anchor = reply_message(actor=user(2, "Bob"), message_id=77, text="чайник точно сломан")

    first = asyncio.run(service.capture_message(message(actor=user(1, "Alice"), message_id=78, text="нет", reply=anchor)))
    second = asyncio.run(service.capture_message(message(actor=user(3, "Carol"), message_id=79, text="включи его", reply=anchor)))

    assert first == second
    candidate = candidates.get_candidate(first)
    assert candidate.reply_count == 2
    assert candidate.score >= service.config.REPLY_WEIGHT * 2
    assert set(candidate.source_message_ids) == {77, 78, 79}
    assert set(candidate.participant_ids) == {1, 2, 3}


def test_reactions_create_strong_candidate_and_later_reactions_update_it(monkeypatch, tmp_path):
    service, candidates, _events, _backfill = configure_service(monkeypatch, tmp_path)
    for actor_id in range(1, 7):
        asyncio.run(service.capture_reaction(reaction_update(actor=user(actor_id), message_id=91)))

    candidate = candidates.due_candidates(datetime.now(timezone.utc) + timedelta(hours=1))[0]
    assert candidate.reaction_count == 6
    assert candidate.unique_reactors == 6
    assert candidate.score >= service.config.CANDIDATE_THRESHOLD

    before = candidate.score
    asyncio.run(service.capture_reaction(reaction_update(actor=user(7), message_id=91)))
    updated = candidates.get_candidate(candidate.id)
    assert updated.reaction_count == 7
    assert updated.score > before


def test_anonymous_reaction_count_does_not_invent_unique_reactors(monkeypatch, tmp_path):
    service, candidates, _events, _backfill = configure_service(monkeypatch, tmp_path)
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1001234567890, type="supergroup"),
        message_id=92,
        date=datetime.now(timezone.utc),
        reactions=[SimpleNamespace(total_count=11), SimpleNamespace(total_count=3)],
    )

    asyncio.run(service.capture_reaction_count(update))

    candidate = candidates.due_candidates(datetime.now(timezone.utc) + timedelta(hours=1))[0]
    assert candidate.reaction_count == 14
    assert candidate.unique_reactors == 0


def test_many_text_emoji_are_far_weaker_than_telegram_reactions():
    from features.chronicle.scoring import reaction_score, text_signal

    emoji_score = text_signal("😂" * 50)
    telegram_score = reaction_score(10, 6, 1.0, 4.0)

    assert emoji_score <= 0.35
    assert telegram_score > emoji_score * 10


def test_ai_reject_does_not_save_event(monkeypatch, tmp_path):
    service, candidates, events, _backfill = configure_service(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc) - timedelta(minutes=20)
    candidate_id = candidates.upsert_candidate(
        chat_id=-1001,
        candidate_key="test:reject",
        timestamp=now,
        due_at=now,
        score_floor=6.0,
        anchor_message_id=10,
        participants=[{"id": 1, "name": "Alice", "username": "alice"}],
        source_message_ids=[10],
    )

    async def reject(_candidate, _context):
        from features.chronicle.ai import ChronicleDecision
        return ChronicleDecision(False, None, "boring", 0.9)

    monkeypatch.setattr(service, "classify_candidate", reject)
    asyncio.run(service.finalize_candidate(candidates.get_candidate(candidate_id)))

    assert events.list(-1001) == []
    assert candidates.get_candidate(candidate_id) is not None


def test_ai_accept_saves_structured_event_and_survives_reopen(monkeypatch, tmp_path):
    service, candidates, events, _backfill = configure_service(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc) - timedelta(minutes=20)
    candidate_id = candidates.upsert_candidate(
        chat_id=-1002,
        candidate_key="test:accept",
        timestamp=now,
        due_at=now,
        score_floor=6.0,
        anchor_message_id=44,
        reaction_count=9,
        unique_reactors=6,
        reply_delta=4,
        participants=[{"id": 2, "name": "Bob", "username": "bob"}],
        source_message_ids=[44, 45, 46],
    )

    async def accept(candidate, _context):
        return accepted_decision(candidate.participant_ids)

    monkeypatch.setattr(service, "classify_candidate", accept)
    event_id = asyncio.run(service.finalize_candidate(candidates.get_candidate(candidate_id)))

    assert event_id
    saved = events.list(-1002)[0]
    assert saved.title == "Великая война за чайник"
    assert saved.participant_ids == [2]
    assert saved.source_message_ids == [44, 45, 46]
    assert saved.reaction_count == 9

    from infrastructure.persistence.sqlite_chronicle_events import SQLiteChronicleEventStore
    reopened = SQLiteChronicleEventStore(tmp_path / "history.db")
    restored = reopened.list(-1002)[0]
    assert restored.id == event_id
    assert restored.summary == saved.summary


def test_duplicate_event_is_detected_before_second_save(monkeypatch, tmp_path):
    service, candidates, events, _backfill = configure_service(monkeypatch, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=1)
    events.save(
        chat_id=-1003,
        started_at=old,
        ended_at=old,
        title="Великая война за чайник",
        summary="Боб спорил с чайником и проиграл.",
        category="local_meme",
        importance_score=7.0,
        anchor_message_id=1,
        reaction_count=5,
        unique_reactors=4,
        reply_count=3,
        keywords=["чайник"],
        entities=["чайник"],
        related_event_ids=[],
        ai_metadata={},
        source="live",
        participants=[{"id": 2, "name": "Bob", "username": "bob"}],
        source_message_ids=[1],
    )
    candidate_id = candidates.upsert_candidate(
        chat_id=-1003,
        candidate_key="test:duplicate",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=20),
        due_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        score_floor=8.0,
        participants=[{"id": 2, "name": "Bob", "username": "bob"}],
        source_message_ids=[100],
    )

    async def accept(candidate, _context):
        return accepted_decision(
            candidate.participant_ids,
            summary="Боб спорил с чайником и снова проиграл.",
        )

    monkeypatch.setattr(service, "classify_candidate", accept)
    result = asyncio.run(service.finalize_candidate(candidates.get_candidate(candidate_id)))

    assert result is None
    assert len(events.list(-1003, limit=10)) == 1


def test_participant_daily_cap_blocks_one_user_spam(monkeypatch, tmp_path):
    service, candidates, events, _backfill = configure_service(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    person = [{"id": 9, "name": "Spammer", "username": "spam"}]
    for index in range(service.config.MAX_EVENTS_PER_USER_24H):
        events.save(
            chat_id=-1004,
            started_at=now - timedelta(hours=index + 1),
            ended_at=now - timedelta(hours=index + 1),
            title=f"Событие {index}", summary="Уже было.", category="other",
            importance_score=6.0, anchor_message_id=index + 1,
            reaction_count=0, unique_reactors=0, reply_count=0,
            keywords=[], entities=[], related_event_ids=[], ai_metadata={}, source="live",
            participants=person, source_message_ids=[index + 1],
        )
    candidate_id = candidates.upsert_candidate(
        chat_id=-1004, candidate_key="spam", timestamp=now - timedelta(minutes=20),
        due_at=now - timedelta(minutes=10), score_floor=5.1,
        participants=person, source_message_ids=[99],
    )

    async def accept(candidate, _context):
        return accepted_decision(candidate.participant_ids, title="Ещё одно событие", summary="Спаммер снова отличился.")

    monkeypatch.setattr(service, "classify_candidate", accept)
    result = asyncio.run(service.finalize_candidate(candidates.get_candidate(candidate_id)))
    assert result is None
    assert len(events.list(-1004, limit=20)) == service.config.MAX_EVENTS_PER_USER_24H


def test_event_store_filters_chronicle_by_participant(tmp_path):
    _candidates, events, _backfill = build_stores(tmp_path)
    now = datetime.now(timezone.utc)
    for event_id, participant in enumerate((1, 2), start=1):
        events.save(
            chat_id=-1005, started_at=now, ended_at=now,
            title=f"Event {event_id}", summary="summary", category="other", importance_score=5,
            anchor_message_id=event_id, reaction_count=0, unique_reactors=0, reply_count=0,
            keywords=[], entities=[], related_event_ids=[], ai_metadata={}, source="live",
            participants=[{"id": participant, "name": f"P{participant}", "username": f"p{participant}"}],
            source_message_ids=[event_id],
        )

    assert [event.title for event in events.list(-1005, user_id=1)] == ["Event 1"]
    assert [event.title for event in events.list(-1005, username="p2")] == ["Event 2"]


def test_chronicle_command_uses_current_chat_and_user_filter(monkeypatch):
    import handlers.chronicle as handler

    calls = []
    replies = []

    async def fake_backfill(chat_id):
        calls.append(("backfill", chat_id))

    async def fake_list(chat_id, **kwargs):
        calls.append(("list", chat_id, kwargs))
        return []

    async def reply(text, **kwargs):
        replies.append((text, kwargs))

    monkeypatch.setattr(handler, "request_backfill", fake_backfill)
    monkeypatch.setattr(handler, "list_events", fake_list)
    msg = SimpleNamespace(
        text="летопись @alice",
        chat=SimpleNamespace(id=-777, type="supergroup", username=None),
        reply_to_message=None,
        reply=reply,
    )

    asyncio.run(handler.handle_chronicle(msg))

    assert calls[0] == ("backfill", -777)
    assert calls[1] == ("list", -777, {"user_id": None, "username": "alice"})
    assert "исторических преступлений" in replies[0][0]


def test_chronicle_failure_never_breaks_normal_message_processing(monkeypatch):
    import features.chronicle.integration as integration
    import features.social_graph.service as social

    async def social_ok(_event):
        return 0

    async def chronicle_boom(_event):
        raise RuntimeError("AI/storage unavailable")

    async def final_handler(_event, _data):
        return "normal-result"

    monkeypatch.setattr(social, "capture_message", social_ok)
    monkeypatch.setattr(integration, "capture_chronicle_message", chronicle_boom)
    middleware = integration.ChronicleSocialInteractionMiddleware()
    result = asyncio.run(middleware(final_handler, message(actor=user(1)), {}))

    assert result == "normal-result"


def test_backfill_phrase_extraction_finds_repeatable_memes():
    from features.chronicle.backfill import _phrases

    phrases = _phrases("главное чайник сначала включи а потом обвиняй производителя")
    assert any("чайник" in phrase and "включи" in phrase for phrase in phrases)
