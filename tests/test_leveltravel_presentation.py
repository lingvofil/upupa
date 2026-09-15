from AI import leveltravel, leveltravel_presentation


def test_format_tours_message_empty_result():
    assert leveltravel_presentation.format_tours_message([], {}, {}) == "😢 Туры не найдены"


def test_format_tours_message_preserves_single_destination_layout():
    params = {
        "search_type": leveltravel.SEARCH_TYPE_TOUR,
        "country_name": "вьетнам",
        "adults": 2,
        "nights": 7,
    }
    date_stats = {
        "searched_dates": 8,
        "min_price": 100000,
        "median_price": 120000,
        "max_price": 180000,
    }
    tours = [
        {
            "hotel_name": "Test Hotel",
            "link": "https://level.travel/hotels/test",
            "scenario": "Value King",
            "date": "18.05.2026",
            "nights": 7,
            "stars": 4,
            "rating": 8.7,
            "location": "Phu Quoc",
            "ai_reason": "Хороший баланс",
            "price": 100000,
            "price_vs_median": -16.7,
        }
    ]

    result = leveltravel_presentation.format_tours_message(
        tours, params, date_stats
    )

    assert result == (
        "🏖 <b>Топ подборка: Вьетнам</b>\n"
        "📍 Тип: Туры\n"
        "👥 2 взр. | 🌙 7 ночей\n\n"
        "📊 <b>Анализ:</b>\n"
        "• Проверено дат: 8\n"
        "• Минимум: 100,000 ₽\n"
        "• Медиана: 120,000 ₽\n"
        "• Максимум: 180,000 ₽\n"
        "\n\n<b>1. <a href='https://level.travel/hotels/test'>Test Hotel</a></b>\n"
        "🎯 <i>Value King</i>\n"
        "⭐️⭐️⭐️⭐️ | 📅 18.05.2026-25.05.2026\n"
        "📊 Рейтинг Level.Travel: 8.7\n"
        "📍 Phu Quoc\n"
        "🤖 <i>Хороший баланс</i>\n"
        "💰 <b>100,000 ₽</b> 🔥 Выгодно!"
    )


def test_format_tours_message_preserves_multi_destination_hotel_header():
    params = {
        "search_type": leveltravel.SEARCH_TYPE_HOTEL,
        "adults": 3,
        "nights": 5,
    }
    search_info = {
        "countries": ["фукуок (VN)", "гоа (IN)"],
        "start_date": "01.12.2026",
        "nights": 5,
    }
    tours = [
        {
            "hotel_name": "Hotel",
            "price": 55000,
            "country_name": "гоа",
            "date": "bad-date",
            "nights": 5,
            "price_vs_median": -7,
        }
    ]

    result = leveltravel_presentation.format_tours_message(
        tours, params, {}, search_info
    )

    assert result.startswith(
        "🏨 <b>Топ подборка: фукуок (VN), гоа (IN)</b>\n"
        "📍 Тип: Отели\n"
        "👥 3 взр. | 🌙 5 ночей\n"
        "📅 Даты: 01.12.2026 - 06.12.2026\n\n"
    )
    assert "🌍 Гоа" in result
    assert "📅 bad-date" in result
    assert "💰 <b>55,000 ₽</b> ✅" in result


def test_format_search_header_matches_transport_layout():
    params = {
        "search_type": leveltravel.SEARCH_TYPE_TOUR,
        "country_name": "гоа",
        "adults": 2,
        "nights": 8,
    }
    date_stats = {
        "searched_dates": 11,
        "min_price": 91000,
        "median_price": 105500,
        "max_price": 140000,
    }

    assert leveltravel_presentation.format_search_header(
        params,
        date_stats,
        include_screenshot_note=True,
    ) == (
        "🏖 <b>Топ подборка: Гоа</b>\n"
        "📍 Тип: Туры\n"
        "👥 2 взр. | 🌙 8 ночей\n\n"
        "📊 <b>Анализ:</b>\n"
        "• Проверено дат: 11\n"
        "• Минимум: 91,000 ₽\n"
        "• Медиана: 105,500 ₽\n"
        "• Максимум: 140,000 ₽\n\n"
        "📸 В каждом сообщении 2 скриншота:\n"
        "1. Календарь цен\n"
        "2. Варианты номеров\n"
    )


def test_format_tour_card_matches_transport_layout():
    params = {"nights": 7}
    tour = {
        "hotel_name": "Hotel",
        "link": "https://level.travel/hotels/hotel",
        "scenario": "Дешевле медианы",
        "country_name": "фукуок",
        "date": "18.05.2026",
        "nights": 7,
        "stars": 3,
        "rating": 8.2,
        "location": "Long Beach",
        "ai_reason": "Хорошая цена",
        "price": 99000,
        "price_vs_median": -7,
    }

    assert leveltravel_presentation.format_tour_card(tour, 2, params) == (
        "<b>2. <a href='https://level.travel/hotels/hotel'>Hotel</a></b>\n"
        "🎯 <i>Дешевле медианы</i>\n"
        "🌍 Фукуок\n"
        "⭐️⭐️⭐️ | 📅 18.05.2026-25.05.2026\n"
        "📊 Рейтинг Level.Travel: 8.2\n"
        "📍 Long Beach\n"
        "🤖 <i>Хорошая цена</i>\n"
        "💰 <b>99,000 ₽</b> ✅"
    )


def test_leveltravel_reexports_presentation_api():
    assert (
        leveltravel.format_tours_message
        is leveltravel_presentation.format_tours_message
    )
