from handlers.stats_lexicon import (
    TELEGRAM_TEXT_LIMIT,
    _split_html_message,
    format_model_usage_message,
)


def test_split_html_message_keeps_chunks_under_telegram_limit():
    sections = [
        "\n".join(
            [
                f"<b>Раздел {section}</b>",
                *[f"• строка {section}-{index}: " + ("x" * 180) for index in range(5)],
            ]
        )
        for section in range(8)
    ]
    text = "\n\n".join(sections)

    chunks = _split_html_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in chunks)
    assert "\n\n".join(chunks) == text
    assert all(not chunk.startswith("• ") for chunk in chunks)


def test_split_html_message_does_not_break_section_when_it_fits():
    intro = "<b>Сводка</b>\n" + ("x" * 3500)
    users = "<b>Топ пользователей</b>\n" + "\n".join(
        f"• пользователь {index}" for index in range(20)
    )
    text = f"{intro}\n\n{users}"

    chunks = _split_html_message(text)

    assert chunks == [intro, users]


def test_split_html_message_preserves_short_report():
    text = "<b>Токены</b>\n• диалог: 123"

    assert _split_html_message(text) == [text]


def test_format_model_usage_message_renders_daily_anomalies():
    report = {
        "totals": {
            "requests": 80,
            "usage_known_requests": 80,
            "total_tokens": 200_000,
            "input_tokens": 150_000,
            "output_tokens": 20_000,
            "reasoning_tokens": 30_000,
            "successful_requests": 80,
            "failed_requests": 0,
        },
        "anomaly_thresholds": {
            "period_hours": 24,
            "total_tokens": 100_000,
            "requests": 50,
        },
        "anomalous_users": [
            {
                "user_id": 42,
                "user_name": "Alice",
                "user_username": "alice",
                "requests": 60,
                "total_tokens": 120_000,
                "share_percent": 60.0,
                "triggered_by_tokens": True,
                "triggered_by_requests": True,
                "chats": [
                    {
                        "chat_id": -1001,
                        "chat_title": "Heavy chat",
                        "requests": 50,
                        "total_tokens": 100_000,
                    }
                ],
                "features": [
                    {
                        "feature": "диалог",
                        "requests": 40,
                        "total_tokens": 90_000,
                    }
                ],
            }
        ],
    }

    rendered = format_model_usage_message(report, "Расход токенов за 24 часа")

    assert "🚨 <b>Аномальная активность за 24 часа</b>" in rendered
    assert "≥100.0 тыс. токенов" in rendered
    assert "≥50 AI-вызовов" in rendered
    assert "Alice (@alice)" in rendered
    assert "60.0% общего расхода" in rendered
    assert "↳ чаты: Heavy chat 100.0 тыс." in rendered
    assert "↳ функции: диалог 90.0 тыс." in rendered
