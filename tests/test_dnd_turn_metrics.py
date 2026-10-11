"""Measured pacing: master steps, local moves, budget exhaustion and failures."""
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_local_engine import action, backend, session

from AI import dnd_ai_budget as budget
from AI import dnd_local_engine as engine
from AI import dnd_turn_lifecycle as lifecycle
from AI import dnd_turn_metrics as metrics


def game():
    return SimpleNamespace(chat_id=-1, turn_metrics={})


def spend(current, calls=1):
    active = budget.current_turn_budget()
    for _ in range(calls):
        active.reserve("prompt", 100).start()


def test_budget_scopes_report_ok_exhausted_and_failed_steps():
    current = game()
    with budget.dnd_turn_budget(current, mode="balanced", turn_id="t1"):
        spend(current, 2)
    with pytest.raises(budget.DndAIBudgetExhausted):
        with budget.dnd_turn_budget(current, mode="economy", turn_id="t2"):
            spend(current, 3)  # economy allows 2
    with pytest.raises(RuntimeError):
        with budget.dnd_turn_budget(current, mode="balanced", turn_id="t3"):
            spend(current, 1)
            raise RuntimeError("provider 503")
    with budget.dnd_turn_budget(current, mode="balanced", turn_id="t4"):
        pass  # never reached a provider: not a master step
    data = metrics.summary(current)
    assert data["ai_steps"] == 3 and data["ai_calls"] == 5
    assert data["exhausted"] == 1 and data["failed"] == 1


def test_nested_scope_is_one_step():
    current = game()
    with budget.dnd_turn_budget(current, turn_id="outer"):
        spend(current)
        with budget.dnd_turn_budget(current, turn_id="inner"):
            spend(current)
    assert metrics.summary(current)["ai_steps"] == 1


def test_local_move_counts_only_when_committed_and_share_is_reported():
    sess = session()
    sess.state = "WAITING_ROLL"
    sess.pending_roll = {"type": "ATTACK", "mode": "NORMAL", "target_user_ids": [1],
                         "attack": {"enemy_key": "огр", "weapon": "меч", "style": "MELEE"}}
    lifecycle.sync_window(sess)
    be, _calls = backend(3, 3)
    engine.execute_action(sess, action(sess, target_id="огр"), backend=be, ai_mode="economy")
    assert sess.turn_metrics["local_ops"] == 1 and sess.turn_metrics["local_kinds"] == {"ATTACK": 1}
    sess.turn_metrics.update(ai_steps=1, ai_calls=2, ai_seconds=12.0, durations=[12.0])
    text = metrics.render(sess)
    assert "Локальные действия без нейросети: 1 (50% всех ходов)" in text and "среднее 12.0 с" in text


def test_metrics_command_is_a_read_only_state_view():
    from AI import dnd_state_commands as commands

    assert "днд метрики" in commands._STATE_ALIASES["metrics"]
    assert "Пока нечего считать" in metrics.render(game())
