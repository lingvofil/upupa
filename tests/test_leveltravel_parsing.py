from AI import leveltravel, leveltravel_parsing


def test_exact_dates_multiple_destinations_and_adults_are_preserved():
    params = leveltravel.parse_search_command(
        "туры фукуок гоа мальдивы 18.05.26-25.05.26 3",
        leveltravel.SEARCH_TYPE_TOUR,
    )

    assert params == {
        "month": None,
        "countries": [
            {"code": "MV", "name": "мальдивы", "location_slug": None},
            {"code": "VN", "name": "фукуок", "location_slug": "Phu.Quoc-VN"},
            {"code": "IN", "name": "гоа", "location_slug": None},
        ],
        "adults": 3,
        "nights": 7,
        "exact_dates": {"start": "18.05.2026", "end": "25.05.2026"},
        "search_type": "tour",
    }


def test_month_search_keeps_explicit_nights_and_hotel_mode():
    params = leveltravel.parse_search_command(
        "отели октябрь шри-ланка 12 ночей 4",
        leveltravel.SEARCH_TYPE_HOTEL,
    )

    assert params["month"] == 10
    assert params["countries"] == [
        {"code": "LK", "name": "шри-ланка", "location_slug": None}
    ]
    assert params["adults"] == 4
    assert params["nights"] == 12
    assert params["exact_dates"] is None
    assert params["search_type"] == "hotel"


def test_country_alias_and_specific_destination_keep_legacy_order():
    params = leveltravel.parse_search_command("туры вьетнам фукуок май 2")

    assert params["countries"] == [
        {"code": "VN", "name": "вьетнам", "location_slug": None},
        {"code": "VN", "name": "фукуок", "location_slug": "Phu.Quoc-VN"},
    ]


def test_invalid_full_date_range_is_not_accepted():
    assert leveltravel.parse_date_range("туры гоа 31.02.26-05.03.26") is None


def test_leveltravel_reexports_parser_api_from_dedicated_module():
    assert leveltravel.parse_date_range is leveltravel_parsing.parse_date_range
    assert leveltravel.calculate_nights is leveltravel_parsing.calculate_nights
    assert leveltravel.parse_search_command is leveltravel_parsing.parse_search_command
    assert leveltravel.MONTH_MAPPING is leveltravel_parsing.MONTH_MAPPING
    assert leveltravel.DESTINATION_MAPPING is leveltravel_parsing.DESTINATION_MAPPING
