from types import SimpleNamespace

import app.diagnostics as diagnostics
from infrastructure.ai.execution import AIExecutionSnapshot


def test_runtime_diagnostics_combines_ai_and_supervisor_metrics(monkeypatch):
    snapshot = AIExecutionSnapshot(
        waiting=2,
        waiting_background=1,
        in_flight=1,
        in_flight_background=0,
        completed=12,
        errors=2,
        queue_timeouts=3,
        request_timeouts=4,
        queue_samples=10,
        queue_wait_avg_ms=25,
        queue_wait_p95_ms=80,
        request_samples=11,
        request_latency_avg_ms=450,
        request_latency_p95_ms=1200,
        provider_samples=9,
        provider_latency_avg_ms=400,
        provider_latency_p95_ms=1000,
    )
    monkeypatch.setattr(diagnostics, "get_ai_execution_snapshot", lambda: snapshot)
    supervisor = SimpleNamespace(
        task_names=("alpha", "beta"),
        recovering_task_names=("beta",),
        restart_count=5,
        restart_counts={"beta": 5},
    )

    payload = diagnostics.build_runtime_diagnostics(supervisor)

    assert payload["ai"]["waiting"] == 2
    assert payload["ai"]["request_latency_p95_ms"] == 1200
    assert payload["background_tasks"] == {
        "active": ["alpha", "beta"],
        "recovering": ["beta"],
        "restart_total": 5,
        "restart_counts": {"beta": 5},
    }
