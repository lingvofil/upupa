import asyncio

from AI import leveltravel, leveltravel_ranking


class _FakeGroq:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.prompts = []

    def generate_text(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def _params():
    return {
        "country_code": "VN",
        "country_name": "вьетнам",
        "countries": [{"code": "VN", "name": "вьетнам"}],
        "month": 12,
        "adults": 2,
        "nights": 7,
        "search_type": leveltravel.SEARCH_TYPE_TOUR,
    }


def _hotels():
    return {
        "cheap": {
            "hotel_name": "Cheap",
            "price": 100000,
            "rating": 7.0,
        },
        "best": {
            "hotel_name": "Best",
            "price": 120000,
            "rating": 9.0,
        },
        "low_rating": {
            "hotel_name": "Low",
            "price": 80000,
            "rating": 5.0,
        },
    }


def test_ai_result_maps_sorted_candidate_indexes(monkeypatch):
    fake = _FakeGroq(
        response=(
            'prefix [{"index": 1, "ai_score": 8, "scenario": "Value", '
            '"reason": "Reason one"}, '
            '{"index": 2, "ai_score": 10, "scenario": "Top", '
            '"reason": "Reason two"}] suffix'
        )
    )
    monkeypatch.setattr(leveltravel_ranking, "groq_ai", fake)

    result = asyncio.run(
        leveltravel_ranking.analyze_tours_with_ai(
            _hotels(),
            {"min_price": 80000, "max_price": 120000, "median_price": 100000},
            _params(),
        )
    )

    # candidates сортируются по цене: Low=0, Cheap=1, Best=2.
    assert [tour["hotel_name"] for tour in result] == ["Best", "Cheap"]
    assert [tour["ai_score"] for tour in result] == [10, 8]
    assert result[0]["scenario"] == "Top"
    assert result[0]["ai_reason"] == "Reason two"
    assert fake.prompts
    assert "Вьетнам" in fake.prompts[0]


def test_fallback_preserves_value_score_order_when_ai_fails(monkeypatch):
    fake = _FakeGroq(error=RuntimeError("boom"))
    monkeypatch.setattr(leveltravel_ranking, "groq_ai", fake)

    result = asyncio.run(
        leveltravel_ranking.analyze_tours_with_ai(
            _hotels(),
            {"min_price": 80000, "max_price": 120000, "median_price": 100000},
            _params(),
        )
    )

    # Low отсекается по rating < 6. Среди оставшихся Best имеет лучший rating/price.
    assert [tour["hotel_name"] for tour in result] == ["Best", "Cheap"]
    assert result[0]["value_score"] == 9.0 / 12.0
    assert "price_vs_median" in result[0]


def test_empty_hotels_skip_ai(monkeypatch):
    fake = _FakeGroq(response="[]")
    monkeypatch.setattr(leveltravel_ranking, "groq_ai", fake)

    result = asyncio.run(
        leveltravel_ranking.analyze_tours_with_ai({}, {}, _params())
    )

    assert result == []
    assert fake.prompts == []


def test_leveltravel_reexports_ranking_api():
    assert leveltravel.DESTINATION_INFO is leveltravel_ranking.DESTINATION_INFO
    assert leveltravel.analyze_tours_with_ai is leveltravel_ranking.analyze_tours_with_ai
