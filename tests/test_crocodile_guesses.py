from games.crocodile import _contains_answer, _is_close_guess, _normalize_guess


def test_normalize_guess_treats_yo_as_e():
    assert _normalize_guess("  ДирижЁр  ") == "дирижер"


def test_answer_can_appear_inside_sentence():
    assert _contains_answer("Мне кажется, это ДирижЁР!", "дирижер") is True


def test_answer_phrase_can_appear_inside_sentence():
    assert _contains_answer("По-моему, это белый медведь, очевидно", "белый медведь") is True


def test_answer_does_not_match_inside_larger_word():
    assert _contains_answer("Это какая-то котлета", "кот") is False


def test_close_guess_accepts_minor_typo():
    assert _is_close_guess("вентилятр", "вентилятор") is True


def test_close_guess_rejects_unrelated_words():
    assert _is_close_guess("детектор", "дирижер") is False


def test_close_guess_does_not_mark_exact_answer_as_close():
    assert _is_close_guess("альпинист", "альпинист") is False


def test_close_guess_ignores_short_words_to_reduce_false_positives():
    assert _is_close_guess("кот", "кит") is False
