from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_channel_persona_prefers_one_readable_thought_over_random_noise():
    from prompts.channel import CHANNEL_PERSONA

    persona = CHANNEL_PERSONA.casefold()

    assert "одна понятная мысль" in persona
    assert "бессвязность" in persona
    assert "не цель" in persona
    assert "рефлексировать" in persona
    assert "второе должно продолжать" in persona


def test_length_modes_keep_short_posts_but_request_coherence():
    from prompts.channel import POST_LENGTH_MODES

    by_name = {mode["name"]: mode for mode in POST_LENGTH_MODES}

    assert "понятная" in by_name["micro"]["instruction"].casefold()
    assert "связную мысль" in by_name["short"]["instruction"].casefold()
    assert "связанных предложения" in by_name["medium"]["instruction"].casefold()


def test_absurd_mode_can_rarely_shout_without_becoming_noise():
    from prompts.channel import POST_CONTENT_MODES

    absurd = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "absurd")
    instruction = absurd["instruction"].casefold()

    assert "капсом" in instruction
    assert "прокричать" in instruction
    assert "не превращай это в постоянный приём" in instruction
    assert "не набор случайных слов" in instruction


def test_chat_and_functionality_modes_are_more_reflective_without_new_logic():
    from prompts.channel import POST_CONTENT_MODES

    by_name = {mode["name"]: mode for mode in POST_CONTENT_MODES}
    chat = by_name["chat"]["instruction"].casefold()
    functionality = by_name["functionality"]["instruction"].casefold()

    assert "конкретную деталь" in chat
    assert "связь с фрагментом" in chat
    assert "наблюдение о себе" in functionality
    assert "собственное противоречие" in functionality
