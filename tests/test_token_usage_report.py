from handlers.stats_lexicon import (
    TELEGRAM_TEXT_LIMIT,
    _split_html_message,
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
