import asyncio
from types import SimpleNamespace

from AI.dnd_style import (
    DND_STORY_MAX_WORDS,
    DND_STYLE_INSTRUCTION,
    _INSULT_SUFFIXES,
    _balance_roll_mode,
    _compact_request_text,
    _compact_story_response,
    _generate_without_consecutive_input,
    _roll_mode,
    errative_text,
)


def test_dnd_style_allows_consecutive_exploratory_party_turns():
    assert "ACTION:INPUT можно ставить несколько эпизодов подряд" in DND_STYLE_INSTRUCTION
    assert "исследуют место" in DND_STYLE_INSTRUCTION
    assert "разговаривают с NPC" in DND_STYLE_INSTRUCTION
    assert "Не заставляй сцену переходить в ROLL или POLL" in DND_STYLE_INSTRUCTION
    assert "предпочитай INPUT" in DND_STYLE_INSTRUCTION


def test_dnd_style_requests_complete_consequences():
    assert "обычно 100–180 слов" in DND_STYLE_INSTRUCTION
    assert "конкретное последствие" in DND_STYLE_INSTRUCTION
    assert "вопрос следующему игроку" in DND_STYLE_INSTRUCTION
    assert DND_STORY_MAX_WORDS == 220


def test_dnd_style_makes_normal_rolls_the_clear_default():
    assert "MODE:NORMAL — штатный режим" in DND_STYLE_INSTRUCTION
    assert "70–80% бросков" in DND_STYLE_INSTRUCTION
    assert "редкие ситуационные исключения" in DND_STYLE_INSTRUCTION
    assert "Не ставь DISADVANTAGE просто потому" in DND_STYLE_INSTRUCTION
    assert "не обязана автоматически\nдавать ADVANTAGE" in DND_STYLE_INSTRUCTION


def test_legacy_hundred_word_hints_are_rewritten():
    text = _compact_request_text(
        "Продолжай до 100 слов. Помни: не более 100 слов. СТРОГО до 100 слов."
    )

    assert "100 слов" not in text
    assert "100–180 слов" in text
    assert "220 слов" in text


def test_story_response_is_hard_capped_and_keeps_action_tag():
    story = " ".join(f"слово{i}" for i in range(90))
    result = _compact_story_response(f"{story} [ACTION:INPUT]")
    visible = result.replace("[ACTION:INPUT]", "").strip()

    assert len(visible.split()) <= DND_STORY_MAX_WORDS
    assert result.endswith("[ACTION:INPUT]")


def test_short_story_response_is_unchanged():
    source = "Короткая сцена. [ACTION:POLL;OPTIONS:Лево;Право]"
    assert _compact_story_response(source) == source


def test_group_generation_keeps_later_actors_consequences_and_action():
    session = SimpleNamespace(conversation=[])
    prompt = (
        "Игроки заявили действия одновременно:\n"
        + "\n".join(f"- Герой{i}: изучаю стену (id={i})" for i in range(1, 5))
        + "\nСначала РАЗРЕШИ КАЖДУЮ заявку."
    )
    story = " ".join(f"слово{i}" for i in range(140))
    response = story + " Четвёртый герой обнаружил выход. [ACTION:INPUT;TARGETS:1]"

    async def generate(current, request):
        assert "до 340 слов" in request
        current.conversation.append({"role": "assistant", "content": response})
        return response

    assert asyncio.run(_generate_without_consecutive_input(generate, session, prompt)) == response
    assert session.conversation[-1]["content"] == response


def test_correction_keeps_group_budget():
    from AI.dnd_group_progress import _correction_prompt

    pending = {"source_prompt": "Игроки заявили действия одновременно:\n- А: ищу\n- Б: слушаю\nСначала РАЗРЕШИ"}
    prompt = _correction_prompt(pending, "Думайте [ACTION:INPUT]", "no-progress")
    response = " ".join(["деталь"] * 110) + " [ACTION:INPUT]"

    async def generate(_session, request):
        assert "до 260 слов" in request
        return response

    assert asyncio.run(_generate_without_consecutive_input(generate, SimpleNamespace(conversation=[]), prompt)) == response


def test_bot_proxy_does_not_append_stock_taunts():
    from AI.dnd_style import _StyledBotProxy

    received = []

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            received.append(text)

    asyncio.run(_StyledBotProxy(Bot()).send_message(1, "🎭 Ход партии."))
    assert received == ["🎭 Ход партии."]


def test_runtime_allows_second_input_without_regeneration():
    session = SimpleNamespace(
        chat_id=-100901,
        conversation=[
            {"role": "assistant", "content": "Сцена. [ACTION:INPUT]"},
        ],
    )
    calls = []

    async def fake_generate(target_session, prompt):
        calls.append(prompt)
        raw = "Осмотр продолжается. [ACTION:INPUT]"
        target_session.conversation.append({"role": "assistant", "content": raw})
        return raw

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "осматриваем рынок")
    )

    assert result.endswith("[ACTION:INPUT]")
    assert len(calls) == 1
    assert session.conversation[-1]["content"].endswith("[ACTION:INPUT]")


def test_runtime_keeps_consecutive_input_on_groq_too():
    session = SimpleNamespace(
        chat_id=-100908,
        conversation=[
            {"role": "assistant", "content": "Сцена. [ACTION:INPUT]"},
        ],
        _dnd_last_generation_provider="groq",
    )
    calls = []

    async def fake_generate(_session, prompt):
        calls.append(prompt)
        return "Можно ещё поговорить со старостой. [ACTION:INPUT]"

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "продолжай")
    )

    assert len(calls) == 1
    assert result.endswith("[ACTION:INPUT]")
    assert "ACTION:POLL" not in result



def test_runtime_guard_allows_input_after_non_input_turn():
    session = SimpleNamespace(
        chat_id=-100903,
        conversation=[
            {"role": "assistant", "content": "Проверка. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:NORMAL]"},
        ],
    )
    calls = []

    async def fake_generate(_session, prompt):
        calls.append(prompt)
        return "Теперь ваш ход. [ACTION:INPUT]"

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "продолжай")
    )

    assert result.endswith("[ACTION:INPUT]")
    assert len(calls) == 1
    assert "продолжай" in calls[0]
    assert "обычно 100–180 слов" in calls[0]


def test_roll_mode_guard_breaks_modified_mode_streaks():
    session = SimpleNamespace(chat_id=-100904, conversation=[])
    history = [
        {"role": "assistant", "content": "Сцена. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:DISADVANTAGE]"},
    ]
    current = "Ещё сцена. [ACTION:ROLL;TYPE:CHECK;DC:11;MODE:DISADVANTAGE]"

    balanced = _balance_roll_mode(session, current, history=history)

    assert _roll_mode(balanced) == "NORMAL"


def test_roll_mode_guard_allows_rare_alternating_modifier_after_normal():
    session = SimpleNamespace(chat_id=-100905, conversation=[])
    history = [
        {"role": "assistant", "content": "Первый. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:DISADVANTAGE]"},
        {"role": "assistant", "content": "Второй. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:NORMAL]"},
    ]
    current = "Третий. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:ADVANTAGE]"

    balanced = _balance_roll_mode(session, current, history=history)

    assert _roll_mode(balanced) == "ADVANTAGE"


def test_roll_mode_guard_caps_same_modifier_inside_recent_window():
    session = SimpleNamespace(chat_id=-100906, conversation=[])
    history = [
        {"role": "assistant", "content": "Один. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:DISADVANTAGE]"},
        {"role": "assistant", "content": "Два. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:NORMAL]"},
        {"role": "assistant", "content": "Три. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:ADVANTAGE]"},
        {"role": "assistant", "content": "Четыре. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:NORMAL]"},
    ]
    current = "Пять. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:DISADVANTAGE]"

    balanced = _balance_roll_mode(session, current, history=history)

    assert _roll_mode(balanced) == "NORMAL"


def test_runtime_guard_persists_balanced_roll_mode_in_conversation():
    session = SimpleNamespace(
        chat_id=-100907,
        conversation=[
            {"role": "assistant", "content": "Сцена. [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:DISADVANTAGE]"},
        ],
    )

    async def fake_generate(target_session, prompt):
        target_session.conversation.append({"role": "user", "content": prompt})
        raw = "Продолжение. [ACTION:ROLL;TYPE:CHECK;DC:11;MODE:DISADVANTAGE]"
        target_session.conversation.append({"role": "assistant", "content": raw})
        return raw

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "продолжай")
    )

    assert _roll_mode(result) == "NORMAL"
    assert _roll_mode(session.conversation[-1]["content"]) == "NORMAL"


def test_dnd_style_keeps_spelling_readable_and_insults():
    source = "Игра с участниками. Игроки, пишите действия и завершить можно позже."
    text = errative_text(
        source,
        add_insult=True,
        taunt_key="style-test",
    )

    assert text.startswith(source)
    assert "Егра" not in text
    assert "учаснег" not in text
    assert "Егроки" not in text
    assert "пешите" not in text
    assert "дейсвия" not in text
    assert "завиршить" not in text
    assert any(suffix.strip() in text for suffix in _INSULT_SUFFIXES)


def test_dnd_style_explicitly_requires_correct_grammar():
    assert "Пиши грамотно" in DND_STYLE_INSTRUCTION
    assert "нормальная орфография, падежи, согласование" in DND_STYLE_INSTRUCTION
    assert "не коверкай слова" in DND_STYLE_INSTRUCTION
    assert "не используй падонковские эрративы" in DND_STYLE_INSTRUCTION


def test_dnd_style_never_stacks_two_automatic_taunts():
    first = errative_text("Первое сообщение.", add_insult=True, taunt_key="no-stack")
    second_pass = errative_text(first, add_insult=True, taunt_key="no-stack")

    matches = [suffix for suffix in _INSULT_SUFFIXES if suffix.strip() in second_pass]
    assert len(matches) == 1
    assert second_pass == first


def test_dnd_style_does_not_repeat_same_taunt_back_to_back_for_chat():
    first = errative_text("Первое сообщение.", add_insult=True, taunt_key="rotation")
    second = errative_text("Второе сообщение.", add_insult=True, taunt_key="rotation")

    first_suffix = next(suffix for suffix in _INSULT_SUFFIXES if suffix.strip() in first)
    second_suffix = next(suffix for suffix in _INSULT_SUFFIXES if suffix.strip() in second)
    assert first_suffix != second_suffix
    assert len(_INSULT_SUFFIXES) >= 12


def test_dnd_style_keeps_mechanical_terms_readable():
    text = errative_text(
        "Сложность — 12. Команда «дальше». КРИТИЧЕСКАЯ УДАЧА.",
        add_insult=True,
        taunt_key="critical-test",
    )

    assert "Сложность — 12" in text
    assert "дальше" in text
    assert "КРИТИЧЕСКАЯ УДАЧА" in text
    assert not any(suffix.strip() in text for suffix in _INSULT_SUFFIXES)
