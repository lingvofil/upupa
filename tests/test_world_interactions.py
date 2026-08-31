import asyncio
from datetime import datetime, timedelta, timezone

from features.world.interactions import (
    INSULT_COOLDOWN,
    insult_cooldown_remaining,
    record_interaction_event,
)
from features.world.news import format_event_fact
from features.world.service import WorldService
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


def _service(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    return WorldService(repo)


def test_interaction_events_are_persisted_and_formatted(tmp_path):
    service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    _run(
        record_interaction_event(
            service,
            "state_visit_showcase",
            actor_state=alpha.world_id,
            target_state=beta.world_id,
            payload={"user_name": "Вася", "text": "главный гараж республики"},
        )
    )
    _run(
        record_interaction_event(
            service,
            "state_insult",
            actor_state=alpha.world_id,
            target_state=beta.world_id,
            payload={"text": "ваша внешняя политика похожа на очередь в МФЦ"},
        )
    )

    events = _run(service.list_events(limit=10))
    state_map = {alpha.world_id: alpha, beta.world_id: beta}
    facts = [format_event_fact(event, state_map) for event in events]

    assert any("Вася" in fact and "главный гараж" in fact for fact in facts)
    assert any("очередь в МФЦ" in fact for fact in facts)


def test_insult_cooldown_is_state_wide_and_expires(tmp_path):
    service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    assert _run(insult_cooldown_remaining(service, alpha.world_id)) is None

    _run(
        record_interaction_event(
            service,
            "state_insult",
            actor_state=alpha.world_id,
            target_state=beta.world_id,
            payload={"text": "дипломатический тычок"},
        )
    )
    event = _run(
        service.list_events(
            limit=1,
            world_id=alpha.world_id,
            event_types={"state_insult"},
        )
    )[0]
    now = event.created_at
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    remaining = _run(insult_cooldown_remaining(service, alpha.world_id, now=now + timedelta(minutes=5)))
    assert remaining is not None
    assert timedelta(minutes=24) < remaining <= INSULT_COOLDOWN

    expired = _run(insult_cooldown_remaining(service, alpha.world_id, now=now + timedelta(minutes=31)))
    assert expired is None
