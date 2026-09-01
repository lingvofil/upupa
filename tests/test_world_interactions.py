import asyncio
from datetime import datetime, timedelta, timezone

from features.world.interactions import (
    INSULT_COOLDOWN,
    VISIT_DURATION,
    expire_due_visits,
    finish_visit,
    get_open_visit,
    insult_cooldown_remaining,
    record_interaction_event,
    visit_is_active,
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


def test_visit_is_active_for_24_hours_and_manual_finish_closes_it(tmp_path):
    service = _service(tmp_path)
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))

    _run(
        record_interaction_event(
            service,
            "state_visit_accepted",
            actor_state=guest.world_id,
            target_state=host.world_id,
            payload={"answered_by": "Посол"},
        )
    )
    visit = _run(get_open_visit(service, host.world_id, guest.world_id))

    assert visit is not None
    assert visit.expires_at - visit.accepted_at == VISIT_DURATION
    assert visit_is_active(visit, now=visit.accepted_at + timedelta(hours=23, minutes=59))
    assert not visit_is_active(visit, now=visit.accepted_at + timedelta(hours=24))

    closed = _run(
        finish_visit(
            service,
            host.world_id,
            guest.world_id,
            reason="manual",
            finished_by="Админ",
        )
    )
    assert closed is not None
    assert _run(get_open_visit(service, host.world_id, guest.world_id)) is None

    event = _run(
        service.list_events(
            limit=1,
            event_types={"state_visit_finished"},
        )
    )[0]
    fact = format_event_fact(event, {host.world_id: host, guest.world_id: guest})
    assert "завершён досрочно" in fact


def test_expired_visit_is_closed_and_both_states_are_notified(tmp_path):
    service = _service(tmp_path)
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    accepted_at = datetime.now(timezone.utc) - timedelta(hours=25)
    service.ledger.record_event(
        "state_visit_accepted",
        actor_state=guest.world_id,
        target_state=host.world_id,
        payload={"answered_by": "Посол"},
        created_at=accepted_at,
    )

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    bot = FakeBot()
    closed = _run(expire_due_visits(bot, service))

    assert closed == 1
    assert _run(get_open_visit(service, host.world_id, guest.world_id)) is None
    assert {chat_id for chat_id, _text in bot.sent} == {host.chat_id, guest.chat_id}
    assert all("24 часа" in text for _chat_id, text in bot.sent)

    event = _run(
        service.list_events(
            limit=1,
            event_types={"state_visit_finished"},
        )
    )[0]
    fact = format_event_fact(event, {host.world_id: host, guest.world_id: guest})
    assert "по истечении 24 часов" in fact
