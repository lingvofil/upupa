"""Юнит-тесты адаптеров google-genai в AI/wrapper.py.

Проверяют конверсию легаси-форматов старого SDK (google.generativeai)
в формат нового (google-genai) без обращения к реальному API.
"""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

from google.genai import types as genai_types

from AI.wrapper import (
    GeminiModel,
    _build_config,
    _normalize_contents,
    _normalize_history,
)


def test_legacy_blob_becomes_part():
    """{"mime_type","data"} -> types.Part (формат шести call-site'ов в коде)."""
    out = _normalize_contents(["опиши", {"mime_type": "image/jpeg", "data": b"\xff\xd8"}])
    assert out[0] == "опиши"
    assert isinstance(out[1], genai_types.Part)
    assert out[1].inline_data.mime_type == "image/jpeg"
    assert out[1].inline_data.data == b"\xff\xd8"


def test_plain_prompt_passthrough():
    assert _normalize_contents("привет") == "привет"


def test_history_with_string_parts():
    """Формат dnd.py: [{'role','parts':[str]}] -> parts оборачиваются в {'text': ...}."""
    out = _normalize_history([{"role": "user", "parts": ["старый текст"]}])
    assert out == [{"role": "user", "parts": [{"text": "старый текст"}]}]


def test_history_empty():
    assert _normalize_history(None) is None
    assert _normalize_history([]) is None


def test_build_config_legacy_safety_dict():
    """Формат summarize.py: dict {категория: порог} -> список SafetySetting."""
    cfg = _build_config({"safety_settings": {
        "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    }})
    assert cfg.safety_settings[0].category == "HARM_CATEGORY_HARASSMENT"
    assert cfg.safety_settings[0].threshold == "BLOCK_NONE"


def test_build_config_generation_config_dict():
    """Формат voice.py (TTS): generation_config={'response_modalities': ...}."""
    cfg = _build_config({"generation_config": {
        "response_modalities": ["AUDIO"],
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": "Puck"}}},
    }})
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"


def test_build_config_empty():
    assert _build_config({}) is None


def test_gemini_model_routes_to_client():
    """GeminiModel передаёт нормализованные параметры в client.models.generate_content."""
    calls = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.update(model=model, contents=contents, config=config)
            return "response"

    class FakeClient:
        models = FakeModels()

    m = GeminiModel(FakeClient(), "gemini-2.5-flash")
    result = m.generate_content(
        ["текст", {"mime_type": "image/png", "data": b"x"}],
        generation_config={"temperature": 0.5},
    )
    assert result == "response"
    assert calls["model"] == "gemini-2.5-flash"
    assert isinstance(calls["contents"][1], genai_types.Part)
    assert calls["config"].temperature == 0.5
