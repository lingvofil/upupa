from types import SimpleNamespace

import pytest

from handlers.media_tools import is_media_speed_command, is_reverse_command


@pytest.mark.parametrize(
    ("text", "caption", "command"),
    [
        ("быстрее", None, "быстрее"),
        (" БыСтРеЕ ", None, "быстрее"),
        (None, "быстрее", "быстрее"),
        (None, " БЫСТРЕЕ ", "быстрее"),
        ("медленнее", None, "медленнее"),
        (" МеДлЕнНеЕ ", None, "медленнее"),
        (None, "медленнее", "медленнее"),
        (None, " МЕДЛЕННЕЕ ", "медленнее"),
    ],
)
def test_speed_command_matches_only_explicit_command(text, caption, command):
    message = SimpleNamespace(text=text, caption=caption)

    assert is_media_speed_command(message, command) is True


@pytest.mark.parametrize(
    ("text", "caption", "command"),
    [
        ("сделай быстрее пожалуйста", None, "быстрее"),
        ("он работает быстрее меня", None, "быстрее"),
        (None, "это видео надо сделать быстрее", "быстрее"),
        ("можно медленнее?", None, "медленнее"),
        ("говори медленнее пожалуйста", None, "медленнее"),
        (None, "тут всё движется медленнее", "медленнее"),
        (None, None, "быстрее"),
        (None, None, "медленнее"),
    ],
)
def test_speed_command_ignores_word_inside_regular_text(text, caption, command):
    message = SimpleNamespace(text=text, caption=caption)

    assert is_media_speed_command(message, command) is False


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
