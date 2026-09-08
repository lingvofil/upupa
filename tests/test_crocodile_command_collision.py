from types import SimpleNamespace

from games import crocodile, reverse_crocodile
from handlers import ROUTERS
from handlers.crocodile_guesses import (
    is_regular_crocodile_answer,
    is_reverse_crocodile_answer,
)


def _message(text: str, *, chat_id: int = -100123, user_id: int = 7):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id, full_name="Игрок"),
    )


def test_crocodile_guess_router_has_priority_over_command_routers():
    names = [router.name for router in ROUTERS]
    assert names[0] == "crocodile_guesses"
    assert names.index("crocodile_guesses") < names.index("media_search")
    assert names.index("crocodile_guesses") < names.index("basic")


def test_regular_answer_can_be_an_animal_command(monkeypatch):
    chat_id = -100123
    monkeypatch.setitem(
        crocodile.game_sessions,
        str(chat_id),
        {"word": "кот", "drawer_id": 42},
    )

    assert is_regular_crocodile_answer(_message("кот", chat_id=chat_id)) is True


def test_regular_answer_can_be_inside_command_like_text(monkeypatch):
    chat_id = -100124
    monkeypatch.setitem(
        crocodile.game_sessions,
        str(chat_id),
        {"word": "кот", "drawer_id": 42},
    )

    assert is_regular_crocodile_answer(_message("найди кот", chat_id=chat_id)) is True


def test_regular_animal_command_is_not_intercepted_outside_game():
    assert is_regular_crocodile_answer(_message("кот", chat_id=-100999)) is False


def test_regular_drawer_cannot_guess_own_command_word(monkeypatch):
    chat_id = -100125
    monkeypatch.setitem(
        crocodile.game_sessions,
        str(chat_id),
        {"word": "опоссум", "drawer_id": 7},
    )

    assert is_regular_crocodile_answer(_message("опоссум", chat_id=chat_id, user_id=7)) is False


def test_reverse_answer_can_be_an_animal_command(monkeypatch):
    chat_id = -100126
    monkeypatch.setitem(
        reverse_crocodile.games,
        str(chat_id),
        {"word": "опоссум"},
    )

    assert is_reverse_crocodile_answer(_message("опоссум", chat_id=chat_id)) is True


def test_reverse_animal_command_is_not_intercepted_outside_game():
    assert is_reverse_crocodile_answer(_message("опоссум", chat_id=-100998)) is False
