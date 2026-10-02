import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_ai_budget as budgets
from AI import dnd_generation_resilience as resilience
from AI.dnd_turn_contract import TURN_CONTRACT
from infrastructure.ai import execution


def _session(**kwargs):
    return SimpleNamespace(chat_id=-1001, active_model="gemini", mode="participants",
                           conversation=[{"role": "user", "content": "legacy rules " * 4_000},
                                         {"role": "assistant", "content": "Погнали."}],
                           **kwargs)


@pytest.fixture
def providers(monkeypatch):
    calls = []
    governor = execution.AIExecutionGovernor(max_concurrency=1, background_max_concurrency=1,
                                            queue_timeout_seconds=0.2, request_timeout_seconds=1)

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="Готово [ACTION:INPUT;TARGETS:1]", candidates=[],
                               usage_metadata=SimpleNamespace(total_token_count=100))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    monkeypatch.setattr(resilience, "_get_client", lambda *_: client)
    monkeypatch.setattr(resilience, "_attempt_pairs", lambda _chat, attempts: [("fake", "gemini-2.5-flash")] * attempts)
    monkeypatch.setattr(resilience, "_circuit_is_open", lambda _chat: False)
    monkeypatch.setattr(resilience, "run_ai_provider_call", governor.run)
    try:
        yield calls, governor
    finally:
        governor.shutdown(wait=True)


def test_primary_and_optional_audits_share_one_turn_allowance(providers):
    calls, _ = providers
    session = _session()

    async def run():
        with budgets.dnd_turn_budget(session, mode="balanced", turn_id="test-turn") as budget:
            await resilience._generate_main_text(session, "Алиса осматривает дверь")
            assert await resilience.generate_auxiliary_text(session, "audit inventory")
            assert await resilience.generate_auxiliary_text(session, "another audit", allow_groq_fallback=True) is None
            assert budget.calls == 2
            assert budget.optional_calls == 1
            assert budget.known_tokens == 200
            assert budget.unknown_usage_calls == 0
            assert budget.estimated_tokens == 200
    asyncio.run(run())
    assert len(calls) == 2


def test_economy_omits_aux_and_exhaustion_preserves_typed_failure(providers):
    calls, _ = providers
    session = _session()

    async def run():
        with budgets.dnd_turn_budget(session, mode="economy") as budget:
            await resilience._generate_main_text(session, "первое продолжение")
            assert await resilience.generate_auxiliary_text(session, "audit", allow_groq_fallback=True) is None
            await resilience._generate_main_text(session, "коррекция в том же ходе")
            with pytest.raises(budgets.DndAIBudgetExhausted, match="сохранена"):
                await resilience._generate_main_text(session, "третья попытка")
            assert budget.calls == 2
    asyncio.run(run())
    assert len(calls) == 2


def test_failed_main_uses_only_one_economy_primary_before_fallback(monkeypatch, providers):
    calls, _ = providers

    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("503 unavailable")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
    monkeypatch.setattr(resilience, "_get_client", lambda *_: client)
    monkeypatch.setattr(resilience, "GROQ_API_KEY", "fake")
    monkeypatch.setattr(resilience, "groq_ai", SimpleNamespace(text_model="test-groq", generate_text=lambda *a, **k: "fallback"))

    async def run():
        with budgets.dnd_turn_budget(_session(), mode="economy") as budget:
            assert await resilience._generate_main_text(_session(), "действие") == "fallback"
            assert budget.calls == 2
    asyncio.run(run())
    assert len(calls) == 1


def test_nested_correction_reuses_budget_but_expired_background_inheritance_gets_fresh():
    with budgets.dnd_turn_budget(mode="balanced", turn_id="first") as first:
        with budgets.dnd_turn_budget(mode="economy", turn_id="nested") as nested:
            assert nested is first
        inherited = budgets.contextvars.copy_context()
    assert first.expired

    def later():
        with budgets.dnd_turn_budget(mode="economy", turn_id="timer") as fresh:
            assert fresh is not first
            assert not fresh.expired
            assert fresh.turn_id == "timer"
    inherited.run(later)


def test_cancelled_groq_waiter_cannot_send_after_a_slot_is_released(monkeypatch):
    governor = execution.AIExecutionGovernor(max_concurrency=1, background_max_concurrency=1,
                                            queue_timeout_seconds=1, request_timeout_seconds=2)
    occupied, release = threading.Event(), threading.Event()
    callers = ThreadPoolExecutor(max_workers=1)
    calls = []

    def block():
        occupied.set()
        release.wait(2)
    first = callers.submit(governor.run, "busy", block)
    monkeypatch.setattr(resilience, "run_ai_provider_call", governor.run)
    monkeypatch.setattr(resilience, "GROQ_API_KEY", "fake")
    monkeypatch.setattr(resilience, "groq_ai", SimpleNamespace(
        text_model="test", generate_text=lambda *a, **k: calls.append(True) or "late"))

    async def run():
        assert occupied.wait(1)
        with budgets.dnd_turn_budget(mode="balanced") as budget:
            task = asyncio.create_task(resilience._bounded_worker(
                resilience._run_groq_sync, _session(), "сцена", timeout_seconds=1))
            for _ in range(100):
                if governor.snapshot().waiting:
                    break
                await asyncio.sleep(0.001)
            assert governor.snapshot().waiting == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            await asyncio.to_thread(first.result, 1)
            for _ in range(100):
                if not governor.snapshot().waiting and not governor.snapshot().in_flight:
                    break
                await asyncio.sleep(0.001)
            assert calls == []
            assert budget.calls == 0
    try:
        asyncio.run(run())
    finally:
        release.set()
        callers.shutdown(wait=True)
        governor.shutdown(wait=True)


def test_expired_deadline_prevents_admission_without_calling_provider(providers):
    calls, _ = providers
    with budgets.dnd_turn_budget(mode="balanced", timeout_seconds=0):
        with pytest.raises(budgets.DndAIBudgetExhausted):
            asyncio.run(resilience._generate_main_text(_session(), "сцена"))
    assert calls == []


def test_compact_contract_keeps_all_active_mechanics_and_never_old_boilerplate():
    session = _session(enemy_combatants={"огр": {}}, inventories={"1": []},
                       conditions={"1": []}, scene_clocks={"alarm": {}})
    system = resilience.build_compact_system(session, "бой предмет слабость шкала")
    assert TURN_CONTRACT in system
    for schema in ("CINEMATIC_ATTACK", "SCENE:UPSERT", "INTENT:SET", "MECH:",
                   "CLOCK:COMPLETE", "CONDITION:ADD", "WEAKNESS:PAYOFF", "OBLIGATION_KIND:"):
        assert schema in system
    assert len(system) <= resilience.DND_GEMINI_SYSTEM_MAX_CHARS
    contents = resilience._history_contents(session, "CURRENT_FACT_CANON")
    sent = "\n".join(part["text"] for row in contents for part in row["parts"])
    assert "CURRENT_FACT_CANON" in sent
    assert "legacy rules" not in sent
    assert sent.count(TURN_CONTRACT) == 1


def test_long_mission_and_all_mechanics_fit_mandatory_system_budget():
    session = _session(
        scene_count=5,
        mission_goal="Ц" * 350,
        spotlight_decisions_since_poll=8,
        enemy_combatants={"огр": {"hp": 20}},
        inventories={"1": [{"name": "ключ"}]},
        conditions={"1": [{"name": "ранен"}]},
        scene_clocks={"alarm": {"value": 1, "max": 4}},
        scene_objects={"cart": {"available": True}},
    )

    system = resilience.build_compact_system(
        session,
        "бой предмет слабость шкала",
        max_chars=resilience.DND_GEMINI_SYSTEM_MAX_CHARS - 256,
        include_optional=False,
    )

    assert TURN_CONTRACT in system
    for schema in ("CINEMATIC_ATTACK", "MECH:", "CLOCK:COMPLETE",
                   "CONDITION:ADD", "OBLIGATION_KIND:"):
        assert schema in system
    assert len(system) <= resilience.DND_GEMINI_SYSTEM_MAX_CHARS - 256


def test_large_durable_current_turn_drops_old_history_before_failing():
    session = _session()
    session.conversation.extend([
        {"role": "user", "content": "OLD_USER_HISTORY " * 700},
        {"role": "assistant", "content": "OLD_MODEL_HISTORY " * 700},
    ])
    request = "LIVE_CURRENT_CANON " + ("x" * 12_000) + " LIVE_CURRENT_TAIL"

    contents = resilience._history_contents(session, request)
    sent = "\n".join(part["text"] for row in contents for part in row["parts"])

    assert request in sent
    assert "LIVE_CURRENT_TAIL" in sent
    assert TURN_CONTRACT in sent
    assert sum(len(part["text"]) for row in contents for part in row["parts"]) <= resilience.DND_GEMINI_INPUT_MAX_CHARS


def test_rich_durable_turn_compacts_memory_before_mandatory_contract():
    from AI.dnd_current_turn_priority import CURRENT_REQUEST_GUARD, CURRENT_REQUEST_MARKER

    session = _session(
        enemy_combatants={"огр": {"hp": 20}},
        inventories={"1": [{"name": "ключ"}]},
        conditions={"1": [{"name": "ранен"}]},
        scene_clocks={"alarm": {"value": 1, "max": 4}},
        scene_objects={"cart": {"available": True}},
    )
    memory = "ПАМЯТЬ DND V2 — АВТОРИТЕТНЫЙ СНИМОК.\n" + ("STATE " * 1_000)
    live_request = "LIVE_ACTION_START " + ("x" * 8_000) + " LIVE_ACTION_END"
    request = (
        memory
        + "\n\n"
        + CURRENT_REQUEST_MARKER
        + ".\n"
        + CURRENT_REQUEST_GUARD
        + "\n\n"
        + live_request
    )

    contents = resilience._history_contents(session, request)
    sent = "\n".join(part["text"] for row in contents for part in row["parts"])

    assert "LIVE_ACTION_START" in sent
    assert "LIVE_ACTION_END" in sent
    assert live_request in sent
    assert TURN_CONTRACT in sent
    assert "Memory v2 сокращена" in sent
    assert sum(len(part["text"]) for row in contents for part in row["parts"]) <= resilience.DND_GEMINI_INPUT_MAX_CHARS


def test_overlong_canonical_current_request_is_not_silently_cut(providers):
    calls, _ = providers
    with pytest.raises(budgets.DndAIBudgetExhausted, match="без обрезки"):
        resilience._history_contents(_session(), "FACT " * 5_000)
    assert calls == []


def test_compact_contract_teaches_new_local_object_and_enemy_policies():
    session = _session(enemy_combatants={"огр": {"hp": 20}},
                       inventories={"1": [{}]}, conditions={"1": [{}]},
                       scene_clocks={"alarm": {}}, scene_objects={"cart": {"available": True}})
    system = resilience.build_compact_system(session, "бой предмет слабость шкала")
    assert "<DND_RULES>" in system
    assert "<DND_ENEMIES>" in system
    assert TURN_CONTRACT in system
    assert len(system) <= resilience.DND_COMPACT_SYSTEM_MAX_CHARS
    session.local_enemy_rules = {"огр": {"policy": "simple"}}
    assert "<DND_ENEMIES>" not in resilience.build_compact_system(session, "бой")


def test_oversized_fallback_never_dispatches_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(resilience, "GROQ_API_KEY", "fake")
    monkeypatch.setattr(resilience, "groq_ai", SimpleNamespace(
        text_model="test", generate_text=lambda *args, **kwargs: calls.append(True)))
    session = _session()
    with budgets.dnd_turn_budget(session, mode="balanced") as budget:
        with pytest.raises(budgets.DndAIBudgetExhausted, match="заявка сохранена"):
            asyncio.run(resilience._run_groq_fallback(session, "UNRESOLVED_CANON " * 1000))
        assert budget.calls == 0
    assert calls == []


def test_service_uses_small_cold_input_without_campaign_history(providers):
    calls, _ = providers
    session = _session(_upupa_ephemeral_generation_depth=1)
    session.conversation[-1]["content"] = "PRIVATE_HISTORY_SENTINEL"
    asyncio.run(resilience._generate_service_text(session, "Служебно создай профиль: JSON style, strength, weakness, special"))
    assert len(calls) == 1
    request = calls[0]
    assert isinstance(request["contents"], str)
    assert "PRIVATE_HISTORY_SENTINEL" not in request["contents"]
    assert len(request["contents"]) <= resilience.DND_SERVICE_INPUT_MAX_CHARS
    assert request["config"].max_output_tokens == 500
    assert request["config"].temperature == 0.1


def test_execution_failure_keeps_requested_model_and_correct_gemini_provider(monkeypatch):
    rows = []
    monkeypatch.setattr(execution, "_AI_USAGE_RECORDER", lambda *args, **kwargs: rows.append((args, kwargs)))
    governor = execution.AIExecutionGovernor(max_concurrency=1, background_max_concurrency=1,
                                            queue_timeout_seconds=0.1, request_timeout_seconds=1)

    def fail(**kwargs):
        raise RuntimeError("fake 429")
    try:
        with pytest.raises(RuntimeError):
            governor.run("dnd.gemini.generate_content", fail, model="models/test-flash")
        assert rows[0][0][2] == "test-flash"
        assert rows[0][1]["provider"] == "gemini"
        assert rows[0][1]["success"] is False
        assert rows[0][1]["total_tokens"] is None
    finally:
        governor.shutdown(wait=True)
