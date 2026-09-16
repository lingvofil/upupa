import asyncio
from datetime import date

from AI import tutu, tutu_presentation, tutu_ranking


def _ticket(price, *, stops=0, baggage=False, airline="Test Air"):
    return {
        "price": price,
        "currency": "RUB",
        "airline": airline,
        "departure": "2026-05-18T10:00:00",
        "arrival": "2026-05-18T12:00:00",
        "duration": "2ч",
        "stops": stops,
        "baggage": baggage,
        "deeplink": "https://avia.tutu.ru/test",
        "trips": [],
    }


def _params():
    return {
        "origins": [{"name": "москва"}],
        "destinations": [{"name": "сочи"}],
        "departure_date": "2026-05-18",
        "return_date": "2026-05-25",
        "passengers": 1,
    }


def test_tutu_facade_reexports_ranking_and_presentation():
    assert tutu.analyze_tickets_with_ai is tutu_ranking.analyze_tickets_with_ai
    assert tutu.format_tickets_message is tutu_presentation.format_tickets_message


def test_ranking_fallback_uses_value_score(monkeypatch):
    monkeypatch.setattr(tutu_ranking, "groq_ai", None)
    tickets = [
        _ticket(15_000, stops=2),
        _ticket(18_000, stops=0),
        _ticket(17_000, stops=1, baggage=True),
    ]

    ranked = asyncio.run(tutu_ranking.analyze_tickets_with_ai(tickets, _params()))

    assert [ticket["price"] for ticket in ranked] == [18_000, 17_000, 15_000]
    assert ranked[0]["value_score"] > ranked[1]["value_score"] > ranked[2]["value_score"]


def test_ranking_maps_valid_ai_json_without_mutating_source(monkeypatch):
    class FakeGroq:
        @staticmethod
        def generate_text(prompt):
            assert "Москва" in prompt
            assert "Сочи" in prompt
            return '[{"index": 1, "ai_score": 9, "scenario": "Прямой рейс", "reason": "Удобный вариант"}]'

    monkeypatch.setattr(tutu_ranking, "groq_ai", FakeGroq())
    tickets = [_ticket(20_000, airline="A"), _ticket(21_000, airline="B")]

    ranked = asyncio.run(tutu_ranking.analyze_tickets_with_ai(tickets, _params()))

    assert len(ranked) == 1
    assert ranked[0]["airline"] == "B"
    assert ranked[0]["ai_score"] == 9
    assert ranked[0]["scenario"] == "Прямой рейс"
    assert ranked[0]["ai_reason"] == "Удобный вариант"
    assert "ai_score" not in tickets[1]


def test_presentation_renders_exact_and_alternative_sections():
    exact = _ticket(42_000, airline="Exact Air")
    exact["meta"] = {
        "date_type": "exact",
        "out_date": date(2026, 5, 18),
        "return_date": date(2026, 5, 25),
    }
    alternative = _ticket(39_000, airline="Alt Air")
    alternative["meta"] = {
        "date_type": "alternative",
        "out_date": date(2026, 5, 17),
        "return_date": date(2026, 5, 24),
    }

    text = tutu_presentation.format_tickets_message(
        [exact],
        [alternative],
        _params(),
    )

    assert "Авиабилеты: Москва → Сочи" in text
    assert "По выбранным датам" in text
    assert "Альтернативные даты" in text
    assert "42,000 ₽" in text
    assert "39,000 ₽" in text
    assert "📅 17.05 – 24.05" in text


def test_presentation_handles_empty_result():
    assert tutu_presentation.format_tickets_message([], [], _params()) == "😢 Билеты не найдены"
