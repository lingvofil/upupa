from handlers.stats_lexicon import (
    TELEGRAM_TEXT_LIMIT,
    _split_html_message,
)


def test_split_html_message_keeps_chunks_under_telegram_limit():
    lines = [
        f"<b>Раздел {index}</b> " + ("x" * 220)
        for index in range(30)
    ]
    text = "\n".join(lines)

    chunks = _split_html_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_html_message_preserves_short_report():
    text = "<b>Токены</b>\n• диалог: 123"

    assert _split_html_message(text) == [text]
