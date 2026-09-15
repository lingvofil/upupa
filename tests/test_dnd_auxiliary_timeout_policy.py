import asyncio

from AI import dnd_generation_resilience as resilience


def test_auxiliary_gemini_deadline_meets_provider_minimum():
    assert resilience.DND_AUX_HTTP_TIMEOUT_MS >= 10_000
    assert (
        resilience.DND_AUX_GOVERNOR_TIMEOUT_SECONDS
        >= resilience.DND_AUX_HTTP_TIMEOUT_MS / 1000
    )


def test_auxiliary_generation_uses_safe_deadlines(monkeypatch):
    captured = {}

    class Session:
        chat_id = -1001

    def fake_run(_session, _prompt, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(resilience, "_circuit_is_open", lambda: False)
    monkeypatch.setattr(resilience, "_run_gemini_sync", fake_run)

    result = asyncio.run(resilience.generate_auxiliary_text(Session(), "audit"))

    assert result == "ok"
    assert captured["http_timeout_ms"] == resilience.DND_AUX_HTTP_TIMEOUT_MS
    assert (
        captured["governor_timeout_seconds"]
        == resilience.DND_AUX_GOVERNOR_TIMEOUT_SECONDS
    )
    assert captured["lane"] == "background"
    assert captured["include_history"] is False
    assert captured["update_circuit"] is False
