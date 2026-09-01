import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from features.world.interactions import (
    finish_visit,
    get_open_visit,
    notify_visit_finished,
    record_interaction_event,
)
from features.world.service import WorldService
from features.world.visit_report import build_visit_report, collect_visit_showcases
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


def _service(path):
    repo = SQLiteWorldRepository(path)
    repo.init_schema()
    return WorldService(repo)


def test_active_visit_survives_service_recreation(tmp_path):
    path = tmp_path / "world.db"
    first = _service(path)
    host = _run(first.enable_state(-1001, "Host"))
    guest = _run(first.enable_state(-1002, "Guest"))
    _run(
        record_interaction_event(
            first,
            "state_visit_accepted",
            actor_state=guest.world_id,
            target_state=host.world_id,
            payload={"answered_by": "Посол"},
        )
    )

    second = _service(path)
    restored = _run(get_open_visit(second, host.world_id, guest.world_id))

    assert restored is not None
    assert restored.host_state == host.world_id
    assert restored.guest_state == guest.world_id


def test_visit_report_reads_every_showcase_without_generic_event_cap(tmp_path):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    accepted_at = datetime.now(timezone.utc) - timedelta(hours=1)

    for index in range(230):
        service.ledger.record_event(
            "state_visit_showcase",
            actor_state=host.world_id,
            target_state=guest.world_id,
            payload={"user_name": f"Гражданин {index % 5}", "text": f"объект №{index}"},
            created_at=accepted_at + timedelta(seconds=index + 1),
        )

    showcases = _run(
        collect_visit_showcases(
            service,
            host_state=host.world_id,
            guest_state=guest.world_id,
            accepted_at=accepted_at,
            finished_at=datetime.now(timezone.utc),
        )
    )

    assert len(showcases) == 230
    assert showcases[0].text == "объект №0"
    assert showcases[-1].text == "объект №229"


def test_finished_visit_sends_factual_report_and_opens_feedback_window(tmp_path):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    accepted_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    service.ledger.record_event(
        "state_visit_accepted",
        actor_state=guest.world_id,
        target_state=host.world_id,
        payload={"answered_by": "Посол"},
        created_at=accepted_at,
    )
    service.ledger.record_event(
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={"user_name": "Вася", "text": "главный гараж республики"},
        created_at=accepted_at + timedelta(minutes=2),
    )
    service.ledger.record_event(
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={"user_name": "Петя", "text": "местный памятник бардаку"},
        created_at=accepted_at + timedelta(minutes=3),
    )

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.next_message_id = 100

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))
            self.next_message_id += 1
            return SimpleNamespace(message_id=self.next_message_id)

    visit = _run(
        finish_visit(
            service,
            host.world_id,
            guest.world_id,
            reason="manual",
            finished_by="Админ",
        )
    )
    assert visit is not None

    bot = FakeBot()
    _run(notify_visit_finished(bot, service, visit, reason="manual"))

    assert len(bot.sent) == 3
    finish_messages = bot.sent[:2]
    assert {chat_id for chat_id, _text in finish_messages} == {host.chat_id, guest.chat_id}
    for _chat_id, text in finish_messages:
        assert "📋 Что удалось показать:" in text
        assert "Вася: главный гараж республики" in text
        assert "Петя: местный памятник бардаку" in text
        assert "Показано: 2 · екскурсоводов: 2" in text
    assert bot.sent[2][0] == guest.chat_id
    assert bot.sent[2][1].startswith("📝 Отзывы об екскурсии")

    reports = _run(
        service.list_events(
            limit=10,
            world_id=host.world_id,
            event_types={"state_visit_report"},
        )
    )
    assert len(reports) == 1
    assert reports[0].payload["showcase_count"] == 2
    assert reports[0].payload["contributor_count"] == 2
    assert "главный гараж" in reports[0].payload["summary"]

    opened = _run(
        service.list_events(
            limit=10,
            world_id=guest.world_id,
            event_types={"state_visit_feedback_opened"},
        )
    )
    assert len(opened) == 1
    assert opened[0].payload["accepted_event_id"] == visit.accepted_event_id


def test_exact_report_excludes_showcases_from_before_this_visit(tmp_path):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    accepted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    service.ledger.record_event(
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={"user_name": "Старый", "text": "прошлая экскурсия"},
        created_at=accepted_at - timedelta(minutes=1),
    )
    service.ledger.record_event(
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={"user_name": "Новый", "text": "нынешняя экскурсия"},
        created_at=accepted_at + timedelta(minutes=1),
    )

    report = _run(
        build_visit_report(
            service,
            host_state=host.world_id,
            guest_state=guest.world_id,
            accepted_at=accepted_at,
            host_chat_id=host.chat_id,
            host_title=host.title,
            guest_title=guest.title,
            finished_at=datetime.now(timezone.utc),
        )
    )

    assert report.showcase_count == 1
    assert "нынешняя экскурсия" in report.text
    assert "прошлая экскурсия" not in report.text
