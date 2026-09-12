from features.channels_settings import _extract_post_title
from prompts.chat_data import CHANNEL_SETTINGS


def test_neural_recipes_requests_post_title():
    assert CHANNEL_SETTINGS["нейрорецепты"]["include_post_title"] is True


def test_extract_post_title_uses_only_first_non_empty_line():
    post_text = "\n  ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 г  \nЭто блюдо прекрасно подойдет...\n#ruGPT"

    assert _extract_post_title(post_text) == "ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 г"


def test_extract_post_title_returns_none_for_empty_text():
    assert _extract_post_title(None) is None
    assert _extract_post_title("\n   \n") is None
