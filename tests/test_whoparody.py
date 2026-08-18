import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def test_profile_prompts_delegate_style_to_current_chat_prompt():
    from AI import whoparody

    assert "Стиль ответа полностью возьми из текущего промпта чата" in whoparody.WHO_AM_I_PROMPT
    assert "Стиль ответа полностью возьми из текущего промпта чата" in whoparody.CHAT_PROFILE_PROMPT
    assert "Будь максимально саркастичным" not in whoparody.WHO_AM_I_PROMPT
    assert "Будь максимально саркастичным" not in whoparody.CHAT_PROFILE_PROMPT


def test_profile_generation_uses_groq_fallback_on_empty_primary_response(monkeypatch):
    from AI import whoparody

    whoparody.chat_settings["12345"] = {"active_model": "gemini"}
    captured = {}

    class EmptyGemini:
        def generate_content(self, prompt, **kwargs):
            captured["gemini_kwargs"] = kwargs
            return SimpleNamespace(text="")

    class FallbackGroq:
        def generate_text(self, prompt):
            captured["groq_prompt"] = prompt
            return "аварийный ответ"

    monkeypatch.setattr(whoparody, "model", EmptyGemini())
    monkeypatch.setattr(whoparody, "groq_ai", FallbackGroq())

    result = asyncio.run(whoparody.generate_with_active_model("тестовый промпт", 12345))

    assert result == "аварийный ответ"
    assert captured["groq_prompt"] == "тестовый промпт"
    assert captured["gemini_kwargs"]["require_text"] is True
    assert captured["gemini_kwargs"]["safety_settings"]["HARM_CATEGORY_HARASSMENT"] == "BLOCK_NONE"


def test_profile_generation_supports_openrouter(monkeypatch):
    from AI import whoparody

    whoparody.chat_settings["12345"] = {"active_model": "openrouter"}

    class OpenRouter:
        def generate_text(self, prompt):
            return "ответ openrouter"

    class GeminiMustNotRun:
        def generate_content(self, *args, **kwargs):
            raise AssertionError("Gemini should not be called for active_model=openrouter")

    monkeypatch.setattr(whoparody, "openrouter_ai", OpenRouter())
    monkeypatch.setattr(whoparody, "model", GeminiMustNotRun())

    result = asyncio.run(whoparody.generate_with_active_model("тест", 12345))

    assert result == "ответ openrouter"
