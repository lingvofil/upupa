import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)
from games import reverse_crocodile as reverse


def test_reverse_guess_ignores_punctuation_in_answer_and_guess():
    assert reverse._contains_reverse_answer("Это точно рокнролл!", "рок-н-ролл")
    assert reverse._contains_reverse_answer("рок—н—ролл", "рок-н-ролл")
    assert reverse._contains_reverse_answer("рок–н–ролл", "рок-н-ролл")
    assert reverse._contains_reverse_answer("к.о.т", "кот")
    assert reverse._contains_reverse_answer("ну погоди", "Ну, погоди!")
    assert reverse._contains_reverse_answer("«ну» (погоди)", "Ну, погоди!")
    assert reverse._contains_reverse_answer("иван да марья", "Иван: да Марья")
    assert reverse._contains_reverse_answer("иван; да… марья?", "Иван: да Марья")
    assert reverse._contains_reverse_answer("точка тире", "точка — тире...")
    assert reverse._normalize_reverse_guess("  Ё-жик?!  ") == "ежик"


def test_reverse_guess_still_requires_word_boundaries():
    assert not reverse._contains_reverse_answer("котик", "кот")
    assert not reverse._contains_reverse_answer("скот", "кот")
    assert not reverse._contains_reverse_answer("рокнролльщик", "рок-н-ролл")


def test_reverse_hints_do_not_count_or_reveal_punctuation():
    session = {"word": "рок-н-ролл", "hints": 0, "revealed_positions": set()}

    hint, new_position = reverse._prepare_next_hint(session)
    assert hint == "💡 В слове 8 букв(ы)."
    assert new_position is None

    positions = reverse._remaining_reveal_positions(session)
    assert 3 not in positions
    assert 5 not in positions
    assert all(session["word"][index].isalnum() for index in positions)


def test_reverse_image_style_is_intentionally_rougher_but_recognizable():
    prompt = reverse._image_prompt_from_clue("На столе лежит простой предмет.")

    assert "дрожащие линии" in prompt
    assert "вылезает за контуры" in prompt
    assert "оставаться узнаваемыми" in prompt


def test_special_mode_minute_tick_sends_hint_and_bumps_drawing(monkeypatch):
    from games import reverse_crocodile_modes as modes

    chat_id = "-42"
    session = {
        "word": "Ну, погоди!",
        "mode": "cartoon",
        "hints": 0,
        "revealed_positions": set(),
        "last_hint_at": None,
    }
    reverse.games[chat_id] = session
    send_hint = AsyncMock(return_value=True)
    send_round = AsyncMock(return_value=True)
    monkeypatch.setattr(reverse, "_send_next_hint", send_hint)
    monkeypatch.setattr(modes, "_send_round", send_round)

    try:
        assert asyncio.run(modes._run_round_tick(chat_id, session)) is True
    finally:
        reverse.games.pop(chat_id, None)

    send_hint.assert_awaited_once_with(chat_id, session)
    send_round.assert_awaited_once_with(chat_id, session, replace=True)


def test_special_mode_answer_uses_punctuation_insensitive_matcher(monkeypatch):
    from games import reverse_crocodile_modes as modes

    chat_id = "-43"
    session = {"word": "Ну, погоди!", "mode": "cartoon"}
    reverse.games[chat_id] = session
    finish = AsyncMock()
    monkeypatch.setattr(modes, "finish_mode", finish)
    monkeypatch.setattr(modes.crocodile, "add_point", lambda *_args, **_kwargs: None)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=int(chat_id)),
        text="ну погоди",
        from_user=SimpleNamespace(id=7, full_name="Игрок"),
    )

    try:
        assert asyncio.run(modes.check_answer(message)) is True
    finally:
        reverse.games.pop(chat_id, None)

    finish.assert_awaited_once()
