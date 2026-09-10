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


def test_dnd_style_limits_free_party_turns_to_every_other_episode():
    assert "НЕ ДВА ПОДРЯД" in DND_STYLE_INSTRUCTION
    assert "не чаще чем через один игровой эпизод" in DND_STYLE_INSTRUCTION
    assert "НИКОГДА не ставь ACTION:INPUT" in DND_STYLE_INSTRUCTION
    assert "предыдущий\nтехнический тег мастера тоже был ACTION:INPUT" in DND_STYLE_INSTRUCTION
    assert "После INPUT следующий эпизод должен завершаться ROLL или POLL" in DND_STYLE_INSTRUCTION
    assert "Не делай длинную цепочку ROLL/POLL" in DND_STYLE_INSTRUCTION


def test_dnd_style_requests_compact_story_text():
    assert "обычно 40–60 слов" in DND_STYLE_INSTRUCTION
    assert "жёсткий максимум 70 слов" in DND_STYLE_INSTRUCTION
    assert "не пересказывай только что случившееся" in DND_STYLE_INSTRUCTION
    assert DND_STORY_MAX_WORDS == 70


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
    assert "40–60 слов" in text
    assert "70 слов" in text


def test_story_response_is_hard_capped_and_keeps_action_tag():
    story = " ".join(f"слово{i}" for i in range(90))
    result = _compact_story_response(f"{story} [ACTION:INPUT]")
    visible = result.replace("[ACTION:INPUT]", "").strip()

    assert len(visible.split()) <= DND_STORY_MAX_WORDS
    assert result.endswith("[ACTION:INPUT]")


def test_short_story_response_is_unchanged():
    source = "Короткая сцена. [ACTION:POLL;OPTIONS:Лево;Право]"
    assert _compact_story_response(source) == source


def test_runtime_guard_regenerates_second_input():
    session = SimpleNamespace(
        chat_id=-100901,
        conversation=[
            {"role": "assistant", "content": "Сцена. [ACTION:INPUT]"},
        ],
    )
    outputs = [
        "Ещё сцена. [ACTION:INPUT]",
        "Теперь проверка. [ACTION:ROLL;TYPE:CHECK;REASON:проверить дверь;DC:10;MODE:NORMAL]",
    ]
    prompts = []

    async def fake_generate(_session, prompt):
        prompts.append(prompt)
        return outputs.pop(0)

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "продолжай")
    )

    assert "ACTION:ROLL" in result
    assert len(prompts) == 2
    assert "не используй ACTION:INPUT" in prompts[1]
    assert all("обычно 40–60 слов" in prompt for prompt in prompts)


def test_runtime_guard_falls_back_to_poll_if_model_ignores_corrections():
    session = SimpleNamespace(
        chat_id=-100902,
        conversation=[
            {"role": "assistant", "content": "Сцена. [ACTION:INPUT]"},
        ],
    )

    async def fake_generate(_session, _prompt):
        return "Упрямый мастер. [ACTION:INPUT;TARGETS:11,22]"

    result = asyncio.run(
        _generate_without_consecutive_input(fake_generate, session, "продолжай")
    )

    assert "ACTION:INPUT" not in result
    assert "ACTION:POLL;TARGETS:11,22" in result
    assert "Действовать осторожно" in result


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
    assert "обычно 40–60 слов" in calls[0]


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


def test_dnd_style_uses_erratives_and_insults():
    text = errative_text(
        "Игра с участниками. Игроки, пишите действия и завершить можно позже.",
        add_insult=True,
        taunt_key="style-test",
    )

    assert "Егра" in text
    assert "учаснег" in text
    assert "Егроки" in text
    assert "пешите" in text
    assert "дейсвия" in text
    assert "завиршить" in text
    assert any(suffix.strip() in text for suffix in _INSULT_SUFFIXES)


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
