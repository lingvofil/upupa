import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Настраивает fake env и моки тяжёлых библиотек до импорта handlers/AI.
from tests import test_smoke_imports

# Импорт нужен только ради настройки тестового окружения.
del test_smoke_imports

from AI import tutu
from handlers import ai_summary


class _StatusMessage:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit_text(self, text, *args, **kwargs):
        self.edits.append((text, args, kwargs))

    async def delete(self):
        self.deleted = True


class _IncomingMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = SimpleNamespace(id=424242)
        self.chat = SimpleNamespace(id=-100424242)
        self.replies = []
        self.status = _StatusMessage()

    async def reply(self, text, *args, **kwargs):
        self.replies.append((text, args, kwargs))
        if len(self.replies) == 1:
            return self.status
        return SimpleNamespace()


def _ticket(*, date_type: str, out_date: str, return_date: str, price: int):
    return {
        "price": price,
        "currency": "RUB",
        "airline": "Smoke Air",
        "departure": f"{out_date}T10:00:00",
        "arrival": f"{out_date}T12:00:00",
        "duration": "2ч",
        "stops": 0,
        "baggage": True,
        "deeplink": "https://avia.tutu.ru/smoke",
        "trips": [],
        "origin_name": "москва",
        "destination_name": "сочи",
        "meta": {
            "date_type": date_type,
            "out_date": datetime.strptime(out_date, "%Y-%m-%d").date(),
            "return_date": datetime.strptime(return_date, "%Y-%m-%d").date(),
        },
    }


def test_tutu_command_routes_through_handler_search_ranking_and_presentation(monkeypatch):
    """Telegram command -> ai_summary router -> Tutu orchestration, без внешнего I/O."""
    message = _IncomingMessage("билеты сочи 18.05.26-25.05.26")
    exact = _ticket(
        date_type="exact",
        out_date="2026-05-18",
        return_date="2026-05-25",
        price=42000,
    )
    alternative = _ticket(
        date_type="alternative",
        out_date="2026-05-17",
        return_date="2026-05-24",
        price=39000,
    )

    async def search_side_effect(
        origins,
        destinations,
        departure_date,
        return_date,
        passengers,
        requested_departure,
        requested_return,
    ):
        assert origins == [{"name": "москва"}]
        assert destinations == [{"name": "сочи"}]
        assert passengers == 1
        assert requested_departure == "2026-05-18"
        assert requested_return == "2026-05-25"
        if departure_date == "2026-05-18":
            assert return_date == "2026-05-25"
            return [exact]
        assert (departure_date, return_date) == ("2026-05-17", "2026-05-24")
        return [alternative]

    multi_destination_search = AsyncMock(side_effect=search_side_effect)
    analyze_tickets_with_ai = AsyncMock(side_effect=lambda tickets, params: tickets)

    monkeypatch.setattr(ai_summary, "BLOCKED_USERS", set())
    monkeypatch.setattr(tutu, "ADMIN_ID", None)
    monkeypatch.setattr(tutu, "multi_destination_search", multi_destination_search)
    monkeypatch.setattr(tutu, "analyze_tickets_with_ai", analyze_tickets_with_ai)
    monkeypatch.setattr(
        tutu,
        "generate_date_variants",
        lambda departure, return_date: [
            (departure, return_date),
            ("2026-05-17", "2026-05-24"),
        ],
    )
    monkeypatch.setattr(tutu.asyncio, "sleep", AsyncMock())

    asyncio.run(
        ai_summary.router.propagate_event(
            update_type="message",
            event=message,
        )
    )

    assert multi_destination_search.await_count == 2
    assert analyze_tickets_with_ai.await_count == 2
    first_rank_args = analyze_tickets_with_ai.await_args_list[0].args
    assert first_rank_args[0] == [exact]
    assert first_rank_args[1]["departure_date"] == "2026-05-18"
    assert first_rank_args[1]["return_date"] == "2026-05-25"

    assert message.status.deleted is True
    assert len(message.status.edits) == 1
    assert "Поиск завершен" in message.status.edits[0][0]

    assert len(message.replies) == 2
    assert "Запускаю поиск билетов" in message.replies[0][0]
    result_text = message.replies[1][0]
    assert "Авиабилеты: Москва → Сочи" in result_text
    assert "По выбранным датам" in result_text
    assert "Альтернативные даты" in result_text
    assert "42,000 ₽" in result_text
    assert "39,000 ₽" in result_text
