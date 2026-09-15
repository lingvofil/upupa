import asyncio
from types import SimpleNamespace

from AI import leveltravel_transport
from AI.leveltravel_parsing import SEARCH_TYPE_TOUR


class _FakeStatusMessage:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class _FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=42)
        self.replies = []
        self.media_groups = []

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def reply_media_group(self, media):
        self.media_groups.append(media)


async def _no_sleep(_delay):
    return None


def test_send_search_results_sends_header_and_text_card(monkeypatch):
    monkeypatch.setattr(leveltravel_transport.asyncio, "sleep", _no_sleep)

    message = _FakeMessage()
    status = _FakeStatusMessage()
    params = {
        "search_type": SEARCH_TYPE_TOUR,
        "country_name": "вьетнам",
        "adults": 2,
        "nights": 7,
    }
    date_stats = {
        "searched_dates": 4,
        "min_price": 100000,
        "median_price": 110000,
        "max_price": 125000,
    }
    tours = [
        {
            "hotel_name": "Text Hotel",
            "link": "#",
            "date": "18.05.2026",
            "nights": 7,
            "price": 100000,
        }
    ]

    asyncio.run(
        leveltravel_transport.send_search_results(
            message,
            status,
            tours,
            params,
            date_stats,
            None,
            SEARCH_TYPE_TOUR,
        )
    )

    assert status.deleted is True
    assert status.edits == [
        (
            "✅ <b>Анализ завершен!</b>\n"
            "Отобрано 1 лучших предложений\n\n"
            "⏳ Создаю скриншоты и формирую отчет...",
            {"parse_mode": "HTML"},
        )
    ]
    assert message.replies[0][0].startswith(
        "🏖 <b>Топ подборка: Вьетнам</b>\n"
    )
    assert "📸 В каждом сообщении 2 скриншота:" in message.replies[0][0]
    assert message.replies[0][1] == {"parse_mode": "HTML"}
    assert message.replies[1] == (
        "<b>1. <a href='#'>Text Hotel</a></b>\n"
        "📅 18.05.2026-25.05.2026\n"
        "💰 <b>100,000 ₽</b>",
        {"parse_mode": "HTML", "disable_web_page_preview": True},
    )
    assert message.media_groups == []


def test_send_search_results_sends_media_and_cleans_files(monkeypatch):
    async def fake_capture(link, name, nights, search_type):
        assert link == "https://level.travel/hotel"
        assert name == "Media Hotel"
        assert nights == 5
        assert search_type == SEARCH_TYPE_TOUR
        return ["/tmp/one.png", "/tmp/two.png"]

    removed = []
    monkeypatch.setattr(
        leveltravel_transport,
        "capture_hotel_screenshots",
        fake_capture,
    )
    monkeypatch.setattr(leveltravel_transport.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(leveltravel_transport.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(leveltravel_transport.os, "remove", removed.append)

    message = _FakeMessage()
    status = _FakeStatusMessage()
    params = {
        "search_type": SEARCH_TYPE_TOUR,
        "country_name": "гоа",
        "adults": 2,
        "nights": 5,
    }
    tours = [
        {
            "hotel_name": "Media Hotel",
            "link": "https://level.travel/hotel",
            "date": "01.11.2026",
            "nights": 5,
            "price": 90000,
        }
    ]

    asyncio.run(
        leveltravel_transport.send_search_results(
            message,
            status,
            tours,
            params,
            {},
            None,
            SEARCH_TYPE_TOUR,
        )
    )

    assert len(message.media_groups) == 1
    media = message.media_groups[0]
    assert len(media) == 2
    assert media[0].caption == (
        "<b>1. <a href='https://level.travel/hotel'>Media Hotel</a></b>\n"
        "📅 01.11.2026-06.11.2026\n"
        "💰 <b>90,000 ₽</b>"
    )
    assert media[1].caption is None
    assert removed == ["/tmp/one.png", "/tmp/two.png"]
