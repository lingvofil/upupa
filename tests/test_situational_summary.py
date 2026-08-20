import asyncio
from types import SimpleNamespace

from AI import situational_summary as ss


def setup_function():
    ss._recent_event_words.clear()
    ss._recent_chat_messages.clear()
    ss._seen_message_ids.clear()


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


def test_one_message_is_enough_for_absurd_summary():
    class FakeRng:
        def random(self):
            return 0.0

        def choice(self, seq):
            if "голубь" in seq:
                return "голубь"
            return seq[0]

    async def model_must_not_be_called(*args, **kwargs):
        raise AssertionError("direct mode should not call the model")

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            123,
            [{"role": "user", "name": "А", "content": "голубь"}],
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


def _message(message_id: int, text: str = "голубь"):
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        caption=None,
        chat=SimpleNamespace(id=77),
        from_user=SimpleNamespace(
            is_bot=False,
            full_name="Детектор",
            first_name="Детектор",
            username="detector",
        ),
    )


def test_register_incoming_message_builds_context_and_rejects_duplicate_message_id():
    message = _message(100, "про голубя и сервер")

    assert ss._register_incoming_message(message) is True
    assert ss._register_incoming_message(message) is False

    context = list(ss._context_for_chat(77))
    assert context == [
        {
            "role": "user",
            "name": "Детектор",
            "content": "про голубя и сервер",
        }
    ]


def test_installer_uses_live_context_and_makes_random_pipeline_idempotent(monkeypatch):
    calls = []

    async def fake_generate(*args, **kwargs):
        return "происходит наблюдение"

    async def original_process(message, *args, **kwargs):
        calls.append(message.message_id)
        return False

    module = SimpleNamespace(
        conversation_history={},
        generate_with_model=fake_generate,
        generate_situational_reaction=object(),
        process_random_reactions=original_process,
    )

    ss.install_into_random_reactions(module)

    message = _message(555, "в чат прилетел голубь")
    first = asyncio.run(module.process_random_reactions(message))
    second = asyncio.run(module.process_random_reactions(message))

    assert first is False
    assert second is False
    assert calls == [555]
    assert list(ss._context_for_chat(77))[-1]["content"] == "в чат прилетел голубь"

    # Генератор теперь берёт именно живой контекст, даже если conversation_history пуст.
    monkeypatch.setattr(ss, "_DIRECT_WORD_PROBABILITY", 0.0)
    result = asyncio.run(module.generate_situational_reaction(77))
    assert result == "*происходит наблюдение*"


def test_installer_is_idempotent():
    async def fake_generate(*args, **kwargs):
        return "происходит тест"

    async def original_process(message, *args, **kwargs):
        return False

    module = SimpleNamespace(
        conversation_history={},
        generate_with_model=fake_generate,
        generate_situational_reaction=object(),
        process_random_reactions=original_process,
    )

    ss.install_into_random_reactions(module)
    first_wrapper = module.process_random_reactions
    ss.install_into_random_reactions(module)

    assert module.process_random_reactions is first_wrapper
