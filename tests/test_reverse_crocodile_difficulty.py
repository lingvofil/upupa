from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def _callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def test_reverse_crocodile_has_three_disjoint_single_word_difficulty_pools():
    from games import crocodile
    from games.reverse_crocodile_words import DIFFICULTY_LEVELS, WORD_POOLS

    assert DIFFICULTY_LEVELS == ("easy", "medium", "hard")
    assert all(len(WORD_POOLS[level]) >= 50 for level in DIFFICULTY_LEVELS)

    normalized = {}
    for level in DIFFICULTY_LEVELS:
        pool = WORD_POOLS[level]
        assert all(len(word.split()) == 1 for word in pool)
        normalized[level] = {crocodile._normalize_guess(word) for word in pool}
        assert len(normalized[level]) == len(pool)

    assert normalized["easy"].isdisjoint(normalized["medium"])
    assert normalized["easy"].isdisjoint(normalized["hard"])
    assert normalized["medium"].isdisjoint(normalized["hard"])


def test_reverse_crocodile_pools_are_shifted_away_from_trivial_objects():
    from games import crocodile
    from games.reverse_crocodile_words import WORD_POOLS

    all_words = {
        crocodile._normalize_guess(word)
        for pool in WORD_POOLS.values()
        for word in pool
    }
    for trivial in ("кошка", "мяч", "ложка", "морковь", "лимон", "диван"):
        assert crocodile._normalize_guess(trivial) not in all_words

    assert "Лабиринт" in WORD_POOLS["easy"]
    assert "Гравитация" in WORD_POOLS["medium"]
    assert "Парадокс" in WORD_POOLS["hard"]


def test_reverse_crocodile_difficulty_buttons_and_replay_keep_level():
    from games import reverse_crocodile as reverse

    assert _callbacks(reverse._difficulty_keyboard()) == [
        "rcroc_level_easy",
        "rcroc_level_medium",
        "rcroc_level_hard",
    ]
    assert _callbacks(reverse._again_keyboard("hard")) == [
        "rcroc_again_hard",
        "rcroc_choose_level",
    ]
    assert reverse.callback_difficulty("rcroc_level_easy") == "easy"
    assert reverse.callback_difficulty("rcroc_again_hard") == "hard"
    assert reverse.callback_difficulty("rcroc_again_0") == "medium"


def test_reverse_word_picker_uses_requested_pool_and_preserves_other_history(monkeypatch):
    from games import crocodile
    from games import reverse_crocodile_words as words

    easy_key = crocodile._normalize_guess(words.WORD_POOLS["easy"][0])
    medium_key = crocodile._normalize_guess(words.WORD_POOLS["medium"][0])
    written = []

    monkeypatch.setattr(words.persistence, "_load_word_history", lambda: [easy_key, medium_key])
    monkeypatch.setattr(words.persistence, "_write_word_history", lambda history: written.append(list(history)))
    monkeypatch.setattr(words.random, "choice", lambda values: values[0])

    picked = words.pick_reverse_crocodile_word("hard")
    picked_key = crocodile._normalize_guess(picked)

    assert picked in words.WORD_POOLS["hard"]
    assert written == [[easy_key, medium_key, picked_key]]
