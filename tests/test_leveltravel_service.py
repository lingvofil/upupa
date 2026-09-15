import asyncio

from AI import leveltravel_service
from AI.leveltravel_parsing import SEARCH_TYPE_HOTEL, SEARCH_TYPE_TOUR


def test_execute_search_uses_direct_mode_for_exact_dates(monkeypatch):
    calls = []
    expected = {"hotels": [{"hotel_name": "A"}], "date_stats": {}, "search_info": {}}

    async def fake_direct_deep_search(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        leveltravel_service,
        "direct_deep_search",
        fake_direct_deep_search,
    )

    params = {
        "countries": [{"code": "VN", "name": "фукуок"}],
        "exact_dates": {"start": "18.05.2026", "end": "25.05.2026"},
        "adults": 2,
        "nights": 7,
    }

    result = asyncio.run(
        leveltravel_service.execute_search(params, SEARCH_TYPE_TOUR)
    )

    assert result is expected
    assert calls == [
        {
            "countries": params["countries"],
            "start_date": "18.05.2026",
            "adults": 2,
            "nights": 7,
            "search_type": SEARCH_TYPE_TOUR,
        }
    ]


def test_execute_search_uses_two_phase_mode_for_month(monkeypatch):
    calls = []
    expected = {"hotels": [{"hotel_name": "B"}], "date_stats": {}}

    async def fake_two_phase_search(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        leveltravel_service,
        "two_phase_search",
        fake_two_phase_search,
    )

    params = {
        "countries": [{"code": "IN", "name": "гоа"}],
        "month": 11,
        "adults": 3,
        "nights": 5,
    }

    result = asyncio.run(
        leveltravel_service.execute_search(params, SEARCH_TYPE_HOTEL)
    )

    assert result is expected
    assert calls == [
        {
            "country_code": "IN",
            "month": 11,
            "adults": 3,
            "nights": 5,
            "search_type": SEARCH_TYPE_HOTEL,
        }
    ]


def test_rank_search_results_delegates_to_ranking_policy(monkeypatch):
    calls = []
    ranked = [{"hotel_name": "Winner"}]

    async def fake_analyze(hotels, date_stats, params):
        calls.append((hotels, date_stats, params))
        return ranked

    monkeypatch.setattr(
        leveltravel_service,
        "analyze_tours_with_ai",
        fake_analyze,
    )

    hotels = [{"hotel_name": "A"}, {"hotel_name": "B"}]
    date_stats = {"median_price": 100000}
    params = {"adults": 2}

    result = asyncio.run(
        leveltravel_service.rank_search_results(hotels, date_stats, params)
    )

    assert result is ranked
    assert calls == [(hotels, date_stats, params)]
