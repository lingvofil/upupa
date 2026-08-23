"""Tests for the GigaChat infrastructure conversation adapter."""

from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)
from core.settings import SPECIAL_CHAT_ID
from infrastructure.ai import gigachat as gc


def _response(model_name: str, text: str = "ok"):
    return SimpleNamespace(
        model=f"{model_name}:test",
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
    )


def test_legacy_gigachat_facade_reexports_infrastructure_adapter():
    from AI.gigachat_client import GigaChatConversationWrapper as LegacyWrapper

    assert LegacyWrapper is gc.GigaChatConversationWrapper


def test_request_uses_new_endpoint_and_chat_payload(monkeypatch):
    captured = {}

    class FakeGigaChat:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def chat(self, payload):
            captured["payload"] = payload
            return _response(payload.model, "answer")

    monkeypatch.setattr(gc, "GigaChat", FakeGigaChat)

    wrapper = gc.GigaChatConversationWrapper(
        "fake-key",
        ["GigaChat-2"],
        ["GigaChat-3-Ultra"],
    )
    result = wrapper.generate_content(
        "привет",
        chat_id=123,
        temperature=0.35,
        max_tokens=321,
    )

    assert result.text == "answer"
    assert captured["client_kwargs"]["base_url"] == "https://api.giga.chat/v1"
    assert "temperature" not in captured["client_kwargs"]
    assert "max_tokens" not in captured["client_kwargs"]

    payload = captured["payload"]
    assert payload.model == "GigaChat-2"
    assert payload.temperature == 0.35
    assert payload.max_tokens == 321
    assert payload.messages[0].content == "привет"


def test_special_chat_falls_back_and_remembers_model_per_chat(monkeypatch):
    attempted_models = []

    class FakeGigaChat:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def chat(self, payload):
            attempted_models.append(payload.model)
            if payload.model == "GigaChat-3-Ultra":
                raise RuntimeError("temporary model failure")
            return _response(payload.model)

    monkeypatch.setattr(gc, "GigaChat", FakeGigaChat)

    wrapper = gc.GigaChatConversationWrapper(
        "fake-key",
        ["GigaChat-2", "GigaChat-2-Pro"],
        ["GigaChat-3-Ultra", "GigaChat-2-Max", "GigaChat-2"],
    )

    assert wrapper.get_last_used_model(999) == "GigaChat-2"
    assert wrapper.get_last_used_model(SPECIAL_CHAT_ID) == "GigaChat-3-Ultra"

    wrapper.generate_content("итак", chat_id=SPECIAL_CHAT_ID)

    assert attempted_models == ["GigaChat-3-Ultra", "GigaChat-2-Max"]
    assert wrapper.get_last_used_model(SPECIAL_CHAT_ID) == "GigaChat-2-Max"
    assert wrapper.get_last_used_model(999) == "GigaChat-2"
    assert wrapper.last_used_model_name == "GigaChat-2-Max"


def test_non_text_prompt_is_rejected_before_network():
    wrapper = gc.GigaChatConversationWrapper(
        "fake-key",
        ["GigaChat-2"],
        ["GigaChat-3-Ultra"],
    )

    try:
        wrapper.generate_content(["not", "text"], chat_id=1)
    except TypeError as exc:
        assert "text prompts only" in str(exc)
    else:
        raise AssertionError("Expected TypeError for non-text prompt")
