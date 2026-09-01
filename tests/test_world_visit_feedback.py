import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import features.world.visit_feedback as feedback
from features.world.service import WorldService
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _run(coro):
    return asyncio.run(coro)


def _service(path):
    repo = SQLiteWorldRepository(path)
    repo.init_schema()
    return WorldService(repo)


class FakeBot:
    def __init__(self):
        self.sent = []
        self.next_message_id = 500

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id)


def test_feedback_window_is_exactly_one_hour_and_survives_restart(tmp_path):
    path = tmp_path / "world.db"
    first = _service(path)
    host = _run(first.enable_state(-1001, "Host"))
    guest = _run(first.enable_state(-1002, "Guest"))
    opened_at = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    bot = FakeBot()

    window = _run(
        feedback.open_feedback_window(
            bot,
            first,
            accepted_event_id=77,
            host_state=host.world_id,
            guest_state=guest.world_id,
            host_title=host.title,
            guest_chat_id=guest.chat_id,
            now=opened_at,
        )
    )

    assert window is not None
    assert window.closes_at - window.opened_at == timedelta(hours=1)
    assert bot.sent[-1][0] == guest.chat_id
    assert "В течение часа" in bot.sent[-1][1]
    assert "екскурсии" in bot.sent[-1][1].lower()

    second = _service(path)
    restored = _run(
        feedback.get_feedback_window_for_prompt(
            second,
            guest_state=guest.world_id,
            prompt_message_id=window.prompt_message_id,
        )
    )
    assert restored is not None
    assert restored.accepted_event_id == 77
    assert restored.closes_at == window.closes_at
    assert not restored.closed


def test_feedback_is_grouped_by_author_and_sent_to_host_after_hour(tmp_path, monkeypatch):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    opened_at = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    bot = FakeBot()
    window = _run(
        feedback.open_feedback_window(
            bot,
            service,
            accepted_event_id=88,
            host_state=host.world_id,
            guest_state=guest.world_id,
            host_title=host.title,
            guest_chat_id=guest.chat_id,
            now=opened_at,
        )
    )
    assert window is not None

    _run(
        feedback.record_visit_feedback(
            service,
            window,
            message_id=1001,
            user_id=1,
            user_name="Вася",
            text="гараж великолепен",
        )
    )
    _run(
        feedback.record_visit_feedback(
            service,
            window,
            message_id=1002,
            user_id=2,
            user_name="Петя",
            text="памятник бардаку прекрасен, но кормили плохо",
        )
    )
    _run(
        feedback.record_visit_feedback(
            service,
            window,
            message_id=1003,
            user_id=1,
            user_name="Вася",
            text="ещё понравился подвал",
        )
    )

    async def fake_summary(*args, **kwargs):
        return "Гостям понравились гараж, подвал и памятник, но питание вызвало дипломатические вопросы."

    monkeypatch.setattr(feedback, "_generate_feedback_summary", fake_summary)
    bot.sent.clear()

    closed = _run(
        feedback.expire_due_feedback_windows(
            bot,
            service,
            now=opened_at + timedelta(hours=1),
        )
    )

    assert closed == 1
    assert bot.sent
    assert all(chat_id == host.chat_id for chat_id, _text in bot.sent)
    text = "\n".join(message for _chat_id, message in bot.sent)
    assert "Обработанный итог" in text
    assert "Кто что писал" in text
    assert "Вася" in text and "гараж великолепен" in text and "ещё понравился подвал" in text
    assert "Петя" in text and "кормили плохо" in text
    assert "Отзывов: 3 · авторов: 2" in text

    restored = _service(tmp_path / "world.db")
    persisted = _run(feedback.get_feedback_window_for_visit(restored, 88))
    assert persisted is not None
    assert persisted.closed


def test_feedback_window_does_not_close_early(tmp_path):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    opened_at = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    bot = FakeBot()
    window = _run(
        feedback.open_feedback_window(
            bot,
            service,
            accepted_event_id=89,
            host_state=host.world_id,
            guest_state=guest.world_id,
            host_title=host.title,
            guest_chat_id=guest.chat_id,
            now=opened_at,
        )
    )
    assert window is not None
    bot.sent.clear()

    assert not _run(
        feedback.close_feedback_window(
            bot,
            service,
            window,
            now=opened_at + timedelta(minutes=59, seconds=59),
        )
    )
    assert bot.sent == []


def test_no_feedback_sends_crystalline_indifference_message(tmp_path):
    service = _service(tmp_path / "world.db")
    host = _run(service.enable_state(-1001, "Host"))
    guest = _run(service.enable_state(-1002, "Guest"))
    opened_at = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    bot = FakeBot()
    window = _run(
        feedback.open_feedback_window(
            bot,
            service,
            accepted_event_id=90,
            host_state=host.world_id,
            guest_state=guest.world_id,
            host_title=host.title,
            guest_chat_id=guest.chat_id,
            now=opened_at,
        )
    )
    assert window is not None
    bot.sent.clear()

    closed = _run(
        feedback.close_feedback_window(
            bot,
            service,
            window,
            now=opened_at + timedelta(hours=1),
        )
    )

    assert closed
    assert bot.sent == [
        (
            host.chat_id,
            "📝 Отзывы об екскурсии\n\n"
            f"Государство №{guest.world_id} — {guest.title} получило час на отзывы после екскурсии.\n\n"
            "Никто не аставил отзывав. Всем кристаллически похуй.",
        )
    ]
