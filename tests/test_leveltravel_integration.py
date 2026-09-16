import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Настраивает fake env и моки тяжёлых библиотек до импорта handlers/AI.
from tests import test_smoke_imports

# Импорт нужен только ради настройки тестового окружения.
del test_smoke_imports

from AI import leveltravel
from handlers import ai_summary


class _StatusMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, *args, **kwargs):
        self.edits.append((text, args, kwargs))


class _IncomingMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = SimpleNamespace(id=424242)
        self.chat = SimpleNamespace(id=-100424242)
        self.replies = []
        self.status = _StatusMessage()

    async def reply(self, text, *args, **kwargs):
        self.replies.append((text, args, kwargs))
        return self.status


@pytest.mark.parametrize(
    ("command", "expected_search_type"),
    [
        ("туры фукуок 18.05.26-25.05.26 2", leveltravel.SEARCH_TYPE_TOUR),
        ("отели фукуок 18.05.26-25.05.26 2", leveltravel.SEARCH_TYPE_HOTEL),
    ],
)
def test_leveltravel_command_routes_through_handler_and_orchestration(
    monkeypatch,
    command,
    expected_search_type,
):
    """Telegram command -> ai_summary router -> LevelTravel orchestration, без внешнего I/O."""
    message = _IncomingMessage(command)
    hotels = [{"hotel_name": "Smoke Hotel", "price": 100000}]
    date_stats = {"detailed_tours_count": 1, "searched_dates": 1}
    search_info = {"countries": ["фукуок (VN)"]}
    ranked = [{"hotel_name": "Smoke Hotel", "scenario": "smoke"}]

    execute_search = AsyncMock(
        return_value={
            "hotels": hotels,
            "date_stats": date_stats,
            "search_info": search_info,
        }
    )
    rank_search_results = AsyncMock(return_value=ranked)
    send_search_results = AsyncMock()

    monkeypatch.setattr(ai_summary, "BLOCKED_USERS", set())
    monkeypatch.setattr(leveltravel, "ADMIN_ID", None)
    monkeypatch.setattr(leveltravel, "execute_search", execute_search)
    monkeypatch.setattr(leveltravel, "rank_search_results", rank_search_results)
    monkeypatch.setattr(leveltravel, "send_search_results", send_search_results)

    asyncio.run(
        ai_summary.router.propagate_event(
            update_type="message",
            event=message,
        )
    )

    execute_search.assert_awaited_once()
    params, search_type = execute_search.await_args.args
    assert search_type == expected_search_type
    assert params["search_type"] == expected_search_type
    assert params["countries"] == [
        {"code": "VN", "name": "фукуок", "location_slug": "Phu.Quoc-VN"}
    ]
    assert params["exact_dates"] == {
        "start": "18.05.2026",
        "end": "25.05.2026",
    }
    assert params["adults"] == 2
    assert params["nights"] == 7

    rank_search_results.assert_awaited_once_with(hotels, date_stats, params)
    send_search_results.assert_awaited_once_with(
        message,
        message.status,
        ranked,
        params,
        date_stats,
        search_info,
        expected_search_type,
    )

    assert len(message.replies) == 1
    assert "Запускаю прямой поиск" in message.replies[0][0]
    assert len(message.status.edits) == 1
    assert "Поиск завершен" in message.status.edits[0][0]
