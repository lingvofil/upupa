from types import SimpleNamespace

import pytest

from handlers.media_tools import is_reverse_command


@pytest.mark.parametrize(
    ("text", "caption"),
    [
        ("наоборот", None),
        (" НаОБОРОТ ", None),
        (None, "наоборот"),
        (None, " НАОБОРОТ "),
    ],
)
def test_reverse_command_matches_only_explicit_command(text, caption):
    message = SimpleNamespace(text=text, caption=caption)

    assert is_reverse_command(message) is True


@pytest.mark.parametrize(
    ("text", "caption"),
    [
        ("а сделай наоборот", None),
        ("я думаю наоборот", None),
        ("наоборот было бы лучше", None),
        ("не наоборот", None),
        (None, "в подписи написано наоборот"),
        (None, None),
    ],
)
def test_reverse_command_ignores_word_inside_regular_text(text, caption):
    message = SimpleNamespace(text=text, caption=caption)

    assert is_reverse_command(message) is False
