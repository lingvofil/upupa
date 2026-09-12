from games import reverse_crocodile as reverse


def test_reverse_guess_ignores_punctuation_in_answer_and_guess():
    assert reverse._contains_reverse_answer("Это точно рокнролл!", "рок-н-ролл")
    assert reverse._contains_reverse_answer("рок—н—ролл", "рок-н-ролл")
    assert reverse._contains_reverse_answer("к.о.т", "кот")
    assert reverse._normalize_reverse_guess("  Ё-жик?!  ") == "ежик"


def test_reverse_guess_still_requires_word_boundaries():
    assert not reverse._contains_reverse_answer("котик", "кот")
    assert not reverse._contains_reverse_answer("скот", "кот")


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
