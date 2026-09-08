import asyncio
from types import SimpleNamespace

from AI.dnd_style import (
    DND_STYLE_INSTRUCTION,
    _generate_without_consecutive_input,
    errative_text,
)


def test_dnd_style_limits_free_party_turns_to_every_other_episode():
    assert "НЕ ДВА ПОДРЯД" in DND_STYLE_INSTRUCTION
    assert "не чаще чем через один игровой эпизод" in DND_STYLE_INSTRUCTION
    assert "НИКОГДА не ставь ACTION:INPUT" in DND_STYLE_INSTRUCTION
    assert "предыдущий\nтехнический тег мастера тоже был ACTION:INPUT" in DND_STYLE_INSTRUCTION
    assert "После INPUT следующий эпизод должен завершаться ROLL или POLL" in DND_STYLE_INSTRUCTION
    assert "Не делай длинную цепочку ROLL/POLL" in DND_STYLE_INSTRUCTION


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
    assert calls == ["продолжай"]


def test_dnd_style_uses_erratives_and_insults():
    text = errative_text(
        "Игра с участниками. Игроки, пишите действия и завершить можно позже.",
        add_insult=True,
    )

    assert "Егра" in text
    assert "учаснег" in text
    assert "Егроки" in text
    assert "пешите" in text
    assert "дейсвия" in text
    assert "завиршить" in text
    assert any(word in text for word in ("дегенераты", "мудилы", "кретины", "долбоёбы"))


def test_dnd_style_keeps_mechanical_terms_readable():
    text = errative_text(
        "Сложность — 12. Команда «дальше». КРИТИЧЕСКАЯ УДАЧА.",
        add_insult=True,
    )

    assert "Сложность — 12" in text
    assert "дальше" in text
    assert "КРИТИЧЕСКАЯ УДАЧА" in text
