from features.channels_settings import (
    _extract_post_title,
    _media_source_urls,
    _telegram_preview_url,
)
from prompts.chat_data import CHANNEL_SETTINGS


def test_neural_recipes_requests_post_title():
    assert CHANNEL_SETTINGS["нейрорецепты"]["include_post_title"] is True


def test_extract_post_title_uses_only_first_non_empty_line():
    post_text = "\n  ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 г  \nЭто блюдо прекрасно подойдет...\n#ruGPT"

    assert _extract_post_title(post_text) == "ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 г"


def test_extract_post_title_returns_none_for_empty_text():
    assert _extract_post_title(None) is None
    assert _extract_post_title("\n   \n") is None


def test_tgstat_channel_builds_public_telegram_fallback():
    assert (
        _telegram_preview_url(
            "https://tgstat.ru/channel/@neural_recipes?utm_source=test"
        )
        == "https://t.me/s/neural_recipes"
    )


def test_non_tgstat_channel_has_no_telegram_fallback():
    assert _telegram_preview_url("https://example.com/channel/@neural_recipes") is None


def test_tgstat_channel_prefers_public_telegram_source():
    assert _media_source_urls("https://tgstat.ru/channel/@uhbla") == [
        "https://t.me/s/uhbla",
        "https://tgstat.ru/channel/@uhbla",
    ]


def test_non_tgstat_channel_keeps_original_source_only():
    assert _media_source_urls("https://example.com/feed") == [
        "https://example.com/feed"
    ]
