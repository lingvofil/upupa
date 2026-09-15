import asyncio

from AI import leveltravel, leveltravel_search


async def _no_sleep(_seconds):
    return None


def test_nights_match_keeps_legacy_tolerance():
    assert leveltravel_search.nights_match(6, 7) is True
    assert leveltravel_search.nights_match(7, 7) is True
    assert leveltravel_search.nights_match(8, 7) is True
    assert leveltravel_search.nights_match(5, 7) is False
    assert leveltravel_search.nights_match(9, 7) is False


def test_two_phase_search_selects_cheapest_dates_and_deduplicates(monkeypatch):
    dates = [f"0{i}.12.2026" for i in range(1, 9)]
    prices = {date: 100000 + i * 1000 for i, date in enumerate(dates)}
    deep_calls = []

    async def fake_quick(country_code, date, adults, nights, search_type):
        return prices[date]

    async def fake_deep(country_code, date, adults, nights, search_type):
        deep_calls.append(date)
        idx = dates.index(date)
        return [
            {
                "hotel_name": "Same Hotel",
                "price": 150000 - idx * 1000,
                "rating": 8.0,
                "nights": 7,
            },
            {
                "hotel_name": "Wrong Nights",
                "price": 90000,
                "rating": 9.0,
                "nights": 4,
            },
        ]

    monkeypatch.setattr(leveltravel_search, "generate_full_month_dates", lambda month: dates)
    monkeypatch.setattr(leveltravel_search, "quick_price_scan", fake_quick)
    monkeypatch.setattr(leveltravel_search, "deep_parse_date", fake_deep)
    monkeypatch.setattr(leveltravel_search.asyncio, "sleep", _no_sleep)

    result = asyncio.run(
        leveltravel_search.two_phase_search("VN", 12, 2, 7)
    )

    assert deep_calls == dates[:7]
    assert list(result["hotels"]) == ["same hotel"]
    assert result["hotels"]["same hotel"]["price"] == 144000
    assert result["date_stats"]["all_dates_count"] == 8
    assert result["date_stats"]["searched_dates"] == 8
    assert result["date_stats"]["min_price"] == 100000
    assert result["date_stats"]["max_price"] == 107000
    assert result["date_stats"]["median_price"] == 104000
    assert result["date_stats"]["detailed_tours_count"] == 7


def test_two_phase_search_returns_empty_shape_without_prices(monkeypatch):
    monkeypatch.setattr(
        leveltravel_search,
        "generate_full_month_dates",
        lambda month: ["01.12.2026"],
    )

    async def fake_quick(*args, **kwargs):
        return None

    monkeypatch.setattr(leveltravel_search, "quick_price_scan", fake_quick)
    monkeypatch.setattr(leveltravel_search.asyncio, "sleep", _no_sleep)

    result = asyncio.run(
        leveltravel_search.two_phase_search("VN", 12, 2, 7)
    )

    assert result == {"hotels": {}, "date_stats": {}}


def test_direct_deep_search_keeps_country_keys_and_stats(monkeypatch):
    calls = []

    async def fake_deep(
        country_code,
        date,
        adults,
        nights,
        search_type,
        destination_slug=None,
    ):
        calls.append((country_code, destination_slug))
        if country_code == "VN":
            return [
                {
                    "hotel_name": "Shared",
                    "price": 120000,
                    "rating": 8.5,
                    "nights": 0,
                }
            ]
        return [
            {
                "hotel_name": "Shared",
                "price": 90000,
                "rating": 8.0,
                "nights": 7,
            }
        ]

    monkeypatch.setattr(leveltravel_search, "deep_parse_date", fake_deep)
    monkeypatch.setattr(leveltravel_search.asyncio, "sleep", _no_sleep)

    countries = [
        {"code": "VN", "name": "фукуок", "location_slug": "Phu.Quoc-VN"},
        {"code": "IN", "name": "гоа"},
    ]
    result = asyncio.run(
        leveltravel_search.direct_deep_search(
            countries, "18.05.2026", 2, 7
        )
    )

    assert calls == [("VN", "Phu.Quoc-VN"), ("IN", None)]
    assert set(result["hotels"]) == {"shared_VN", "shared_IN"}
    assert result["hotels"]["shared_VN"]["nights"] == 7
    assert result["date_stats"] == {
        "all_dates_count": 1,
        "searched_dates": 1,
        "min_price": 90000,
        "max_price": 120000,
        "median_price": 120000,
        "detailed_tours_count": 2,
    }
    assert result["search_info"] == {
        "countries": ["фукуок (VN)", "гоа (IN)"],
        "start_date": "18.05.2026",
        "nights": 7,
    }


def test_leveltravel_reexports_search_api():
    assert leveltravel.nights_match is leveltravel_search.nights_match
    assert leveltravel.two_phase_search is leveltravel_search.two_phase_search
    assert leveltravel.direct_deep_search is leveltravel_search.direct_deep_search
