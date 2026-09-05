import asyncio
from types import SimpleNamespace

from AI import situational_summary as ss


def setup_function():
    ss._recent_event_words.clear()
    ss._recent_chat_messages.clear()
    ss._seen_message_ids.clear()


def test_normalize_model_result_accepts_short_complete_summary():
    assert ss._normalize_model_result("*происходит спор о кальяне*") == (
        "происходит спор о кальяне",
        "происходит спор о кальяне",
    )
    assert ss._normalize_model_result("произошел внезапный подъеб.") == (
        "произошёл внезапный подъеб",
        "произошёл внезапный подъеб",
    )
    assert ss._normalize_model_result("произошла смена темы") == (
        "произошла смена темы",
        "произошла смена темы",
    )
    assert ss._normalize_model_result("произошло примирение") == (
        "произошло примирение",
        "произошло примирение",
    )


def test_normalize_model_result_rejects_dangling_adjective():
    assert ss._normalize_model_result("произошёл странный") is None
    assert ss._normalize_model_result("происходит весёлый") is None


def test_normalize_model_result_rejects_wrong_or_too_long_format():
    assert ss._normalize_model_result("это происходит спор") is None
    assert ss._normalize_model_result("происходит очень смешной голубь на сервере") is None
    assert ss._normalize_model_result("происходит спор, но недолго") is None


def test_prompt_requires_contextual_complete_phrase():
    prompt = ss._build_prompt(
        [
            {"role": "user", "name": "А", "content": "давайте кальян"},
            {"role": "user", "name": "Б", "content": "опять спорим куда идти"},
        ],
        ["произошёл спор о пиве"],
    )

    assert "ОСМЫСЛЕННУЮ" in prompt
    assert "нельзя «произошёл странный»" in prompt
    assert "А: давайте кальян" in prompt
    assert "произошёл спор о пиве" in prompt


def test_generator_always_uses_model_and_returns_complete_summary():
    calls = []

    async def fake_model(prompt, chat_id, **kwargs):
        calls.append((prompt, chat_id, kwargs))
        return "произошёл внезапный срач"

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            123,
            [
                {"role": "user", "name": "А", "content": "куда идём"},
                {"role": "user", "name": "Б", "content": "ты опять всё усложнил"},
            ],
            fake_model,
        )
    )

    assert result == "*произошёл внезапный срач*"
    assert len(calls) == 1
    assert calls[0][1] == 123
    assert calls[0][2]["max_tokens"] == 24


def test_one_message_is_enough_but_still_goes_through_model():
    async def fake_model(*args, **kwargs):
        return "произошло голубиное вторжение"

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            123,
            [{"role": "user", "name": "А", "content": "голубь залетел в окно"}],
            fake_model,
        )
    )

    assert result == "*произошло голубиное вторжение*"


def test_invalid_model_answer_is_skipped_instead_of_random_word_fallback():
    async def bad_model(*args, **kwargs):
        return "произошёл странный"

    result = asyncio.run(
        ss.generate_absurd_situational_reaction(
            321,
            [{"role": "user", "name": "А", "content": "что-то странное происходит"}],
            bad_model,
        )
    )

    assert result is None


def test_duplicate_summary_is_skipped_and_recent_context_is_passed_to_prompt():
    prompts = []

    async def same_model(prompt, *args, **kwargs):
        prompts.append(prompt)
        return "произошла смена темы"

    history = [{"role": "user", "name": "А", "content": "давайте о другом"}]
    first = asyncio.run(ss.generate_absurd_situational_reaction(444, history, same_model))
    second = asyncio.run(ss.generate_absurd_situational_reaction(444, history, same_model))

    assert first == "*произошла смена темы*"
    assert second is None
    assert "произошла смена темы" in prompts[1]


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


def test_installer_uses_live_context_and_makes_random_pipeline_idempotent():
    calls = []

    async def fake_generate(*args, **kwargs):
        return "происходит голубиный захват"

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

    result = asyncio.run(module.generate_situational_reaction(77))
    assert result == "*происходит голубиный захват*"


def test_installer_is_idempotent():
    async def fake_generate(*args, **kwargs):
        return "происходит обычный тест"

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
