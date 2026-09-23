import asyncio
from types import SimpleNamespace

from AI import dnd_current_turn_priority as priority


CAMPAIGN_MARKER = "КАМПАНИЯ УПУПЫ: ЖИВОЕ СОСТОЯНИЕ"


def test_campaign_context_moves_before_current_request_and_live_turn_is_last():
    current = (
        "Игроки заявили действия одновременно:\n"
        "- Детектор: CURRENT_ACTION_SENTINEL выпиваю сглыбу\n"
        "- M&M: лечу на вертолёте\n"
        "Разреши их в одной общей сцене."
    )
    context = (
        f"{CAMPAIGN_MARKER}: актуальное состояние.\n"
        "ПАМЯТЬ NPC: старая крыса.\n"
        "СЮЖЕТ: OLD_CONTEXT_SENTINEL"
    )

    prompt = priority.prioritize_current_request(
        current + "\n\n" + context,
        campaign_marker=CAMPAIGN_MARKER,
    )

    assert prompt.index("OLD_CONTEXT_SENTINEL") < prompt.index(priority.CURRENT_REQUEST_MARKER)
    assert prompt.index(priority.CURRENT_REQUEST_MARKER) < prompt.index("CURRENT_ACTION_SENTINEL")
    assert prompt.rstrip().endswith("Разреши их в одной общей сцене.")
    assert "Не исполняй и не пересказывай старые действия заново" in prompt


def test_plain_prompt_without_campaign_context_is_unchanged():
    current = "CURRENT_ONLY_SENTINEL продолжай после броска"

    prompt = priority.prioritize_current_request(
        current,
        campaign_marker=CAMPAIGN_MARKER,
    )

    assert prompt == current


def test_memory_v2_priority_survives_provider_compaction():
    from AI.dnd_generation_resilience import _bounded_current_request

    current = "Алина: обыскать стол (id=1). CURRENT_ACTOR_ACTION\n" + "правила сцены " * 900
    memory = "ПАМЯТЬ DND V2 — АВТОРИТЕТНЫЙ СНИМОК.\n" + "прошлые события " * 800
    prompt = priority.prioritize_current_request(current + "\n\n" + memory, campaign_marker=CAMPAIGN_MARKER)
    for budget in (8000, 4200):
        compact = _bounded_current_request(prompt, budget)
        assert len(compact) <= budget
        assert "Алина: обыскать стол (id=1). CURRENT_ACTOR_ACTION" in compact
        assert priority.CURRENT_REQUEST_MARKER in compact


def test_installed_wrapper_prioritizes_prompt_before_provider_call():
    captured = []

    async def original_generate(_session, prompt):
        captured.append(prompt)
        return "ok"

    dnd = SimpleNamespace(generate_session_response=original_generate)
    priority.install_dnd_current_turn_priority(dnd, campaign_marker=CAMPAIGN_MARKER)

    current = "CURRENT_WRAPPER_SENTINEL свежий ход"
    context = f"{CAMPAIGN_MARKER}: state OLD_WRAPPER_SENTINEL"
    result = asyncio.run(
        dnd.generate_session_response(
            SimpleNamespace(),
            current + "\n\n" + context,
        )
    )

    assert result == "ok"
    assert captured[0].index("OLD_WRAPPER_SENTINEL") < captured[0].index("CURRENT_WRAPPER_SENTINEL")
    assert captured[0].rstrip().endswith(current)
