from types import SimpleNamespace

from AI import dnd_generation_resilience as resilience
from infrastructure.ai.groq import GroqWrapper


def _session():
    return SimpleNamespace(
        chat_id=-1001,
        conversation=[{"role": "user", "content": "system prompt"}],
    )


def test_dnd_groq_call_disables_sdk_retries_and_bounds_http_timeout(monkeypatch):
    calls = []

    def fake_generate_text(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "fallback ok"

    monkeypatch.setattr(resilience, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        resilience,
        "groq_ai",
        SimpleNamespace(generate_text=fake_generate_text),
    )

    result = resilience._run_groq_sync(_session(), "текущий ход")

    assert result == "fallback ok"
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["max_retries"] == 0
    assert kwargs["request_timeout_seconds"] == resilience.DND_GROQ_HTTP_TIMEOUT_SECONDS
    assert 0 < resilience.DND_GROQ_HTTP_TIMEOUT_SECONDS < resilience.DND_GROQ_FALLBACK_TIMEOUT_SECONDS


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


class _FakeClient:
    def __init__(self):
        self.options = None
        self.chat = SimpleNamespace(completions=_FakeCompletions())

    def with_options(self, **kwargs):
        self.options = kwargs
        return self


def test_groq_wrapper_applies_per_call_request_options():
    wrapper = object.__new__(GroqWrapper)
    wrapper.client = _FakeClient()
    wrapper.text_model = "test-model"

    result = wrapper.generate_text(
        "hello",
        max_tokens=123,
        max_retries=0,
        request_timeout_seconds=15.0,
    )

    assert result == "ok"
    assert wrapper.client.options == {"max_retries": 0, "timeout": 15.0}
    assert wrapper.client.chat.completions.kwargs["max_tokens"] == 123
