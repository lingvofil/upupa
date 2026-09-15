from AI import leveltravel, leveltravel_search_plan


def test_package_url_keeps_night_tolerance_and_destination_slug():
    url = leveltravel_search_plan.build_search_url(
        "VN",
        "18.05.2026",
        2,
        7,
        leveltravel.SEARCH_TYPE_TOUR,
        destination_slug="Phu.Quoc-VN",
    )

    assert url == (
        "https://level.travel/search/"
        "Moscow-RU-to-Phu.Quoc-VN-departure-18.05.2026-"
        "for-6..8-nights-2-adults-0-kids-1..5-stars-package-type"
    )


def test_hotel_url_keeps_flexible_checkin_and_checkout_windows():
    url = leveltravel_search_plan.build_search_url(
        "VN",
        "18.05.2026",
        2,
        7,
        leveltravel.SEARCH_TYPE_HOTEL,
        destination_slug="Phu.Quoc-VN",
    )

    assert url == (
        "https://level.travel/search/"
        "Any-RU-to-Phu.Quoc-VN-"
        "departure-from-16.05.2026..20.05.2026-"
        "to-23.05.2026..27.05.2026-"
        "2-adults-0-kids-1..5-stars-hotel-type-"
        "18.05.2026-25.05.2026"
    )


def test_date_range_generation_is_inclusive_and_preserves_invalid_fallback():
    assert leveltravel_search_plan.generate_date_range_list(
        "18.05.2026", "20.05.2026"
    ) == ["18.05.2026", "19.05.2026", "20.05.2026"]
    assert leveltravel_search_plan.generate_date_range_list(
        "invalid", "20.05.2026"
    ) == ["invalid"]


def test_leveltravel_reexports_search_plan_api():
    assert leveltravel.build_search_url is leveltravel_search_plan.build_search_url
    assert (
        leveltravel.generate_full_month_dates
        is leveltravel_search_plan.generate_full_month_dates
    )
    assert (
        leveltravel.generate_date_range_list
        is leveltravel_search_plan.generate_date_range_list
    )
    assert leveltravel.LEVELTRAVEL_WEB_URL == leveltravel_search_plan.LEVELTRAVEL_WEB_URL
