import asyncio
from types import SimpleNamespace

from AI import situational_summary as ss


def setup_function():
    ss._recent_event_words.clear()


def test_normalize_model_result_accepts_strict_two_word_contract():
    assert ss._normalize_model_result("*происходит комментирование*") == (
        "происходит комментирование",
        "комментирование",
    )
    assert ss._normalize_model_result("произошел подъеб.") == (
        "произошёл подъеб",
        "подъеб",
    )


def test_normalize_model_result_rejects_extra_words():
    assert ss._normalize_model_result("происходит очень смешной голубь") is None
    assert ss._normalize_model_result("это происходит голубь") is None


def test_extract_candidate_words_uses_human_chat_and_filters_noise():
    messages = [
        {"role": "user", "content": "А потом прилетел голубь и сел на сервер"},
        {"role": "assistant", "content": "происходит объяснение"},
        {"role": "user", "content": "сервер теперь священный"},
    ]
    words = ss._extract_candidate_words(messages)

    assert "голубь" in words
    assert "сервер" in words
    assert "объяснение" not in words
    assert "потом" not in words


def test_direct_mode_can_make_absurd_event_from_chat_word():
    class FakeRng:
        def random(self):
            return 0.0  # always direct mode

        def choice(self, seq):
            if "голубь" in seq:
                return "голубь"
            return seq[0]  # prefix -> "происходит"

    history = [
        {"role": "user", "name": "А", "content": "ну вот обсуждаем работу"},
        {"role": "user", "name": "Б", "content": "в окно прилетел голубь"},
        {"role": "user", "name": "А", "content": "и все замолчали"},
    ]

    async def model_must_not_be_called(*args, **kwargs):
        raise AssertionError("direct mode should not call the model")

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            123,
            history,
            model_must_not_be_called,
            rng=FakeRng(),
        )
    )
    assert result == "*происходит голубь*"


def test_invalid_model_answer_falls_back_to_word_from_chat():
    class FakeRng:
        def random(self):
            return 0.99  # model mode first

        def choice(self, seq):
            if "подъеб" in seq:
                return "подъеб"
            return seq[-1]  # prefix -> "произошёл"

    history = [
        {"role": "user", "name": "А", "content": "вопрос был простой"},
        {"role": "user", "name": "Б", "content": "это был подъеб"},
        {"role": "user", "name": "А", "content": "понял принято"},
    ]

    async def bad_model(*args, **kwargs):
        return "Сейчас происходит какой-то веселый подъеб"

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            321,
            history,
            bad_model,
            rng=FakeRng(),
        )
    )
    assert result == "*произошёл подъеб*"


def test_installer_replaces_only_situational_generator():
    history = {
        "77": [
            {"role": "user", "content": "один голубь"},
            {"role": "user", "content": "второй голубь"},
            {"role": "user", "content": "третий голубь"},
        ]
    }

    async def fake_generate(*args, **kwargs):
        return "происходит наблюдение"

    old_process = object()
    module = SimpleNamespace(
        conversation_history=history,
        generate_with_model=fake_generate,
        generate_situational_reaction=object(),
        process_random_reactions=old_process,
    )

    ss.install_into_random_reactions(module)

    assert callable(module.generate_situational_reaction)
    assert module.process_random_reactions is old_process
