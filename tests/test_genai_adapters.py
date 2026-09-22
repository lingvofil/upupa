"""Unit tests for the google-genai infrastructure adapters."""

from tests import test_smoke_imports  # noqa: F401  (env + mocks)

from google.genai import types as genai_types

from infrastructure.ai.gemini import (
    GeminiModel,
    ModelFallbackWrapper,
    _build_config,
    _empty_response_details,
    _is_retryable,
    _normalize_contents,
    _normalize_history,
)


def test_legacy_blob_becomes_part():
    out = _normalize_contents(
        ["опиши", {"mime_type": "image/jpeg", "data": b"\xff\xd8"}]
    )
    assert out[0] == "опиши"
    assert isinstance(out[1], genai_types.Part)
    assert out[1].inline_data.mime_type == "image/jpeg"
    assert out[1].inline_data.data == b"\xff\xd8"


def test_plain_prompt_passthrough():
    assert _normalize_contents("привет") == "привет"


def test_history_with_string_parts():
    out = _normalize_history([{"role": "user", "parts": ["старый текст"]}])
    assert out == [{"role": "user", "parts": [{"text": "старый текст"}]}]


def test_history_empty():
    assert _normalize_history(None) is None
    assert _normalize_history([]) is None


def test_build_config_legacy_safety_dict():
    cfg = _build_config(
        {
            "safety_settings": {
                "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
            }
        }
    )
    assert cfg.safety_settings[0].category == "HARM_CATEGORY_HARASSMENT"
    assert cfg.safety_settings[0].threshold == "BLOCK_NONE"


def test_build_config_generation_config_dict():
    cfg = _build_config(
        {
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": "Puck"}
                    }
                },
            }
        }
    )
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"


def test_build_config_empty():
    assert _build_config({}) is None


def test_build_config_ignores_internal_require_text_flag():
    assert _build_config({"require_text": True}) is None


def test_gemini_model_routes_to_client():
    calls = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.update(model=model, contents=contents, config=config)
            return "response"

    class FakeClient:
        models = FakeModels()

    adapter = GeminiModel(FakeClient(), "gemini-2.5-flash")
    result = adapter.generate_content(
        ["текст", {"mime_type": "image/png", "data": b"x"}],
        generation_config={"temperature": 0.5},
    )
    assert result == "response"
    assert calls["model"] == "gemini-2.5-flash"
    assert isinstance(calls["contents"][1], genai_types.Part)
    assert calls["config"].temperature == 0.5


def test_require_text_treats_empty_response_as_failure(monkeypatch):
    class EmptyResponse:
        text = None
        candidates = []

    class FakeGeminiModel:
        def generate_content(self, prompt):
            return EmptyResponse()

    class FakeWrapper(ModelFallbackWrapper):
        def _build_model(self, api_key, model_name):
            return FakeGeminiModel()

    monkeypatch.setattr(
        "infrastructure.ai.gemini._throttle_key",
        lambda api_key: None,
    )

    wrapper = FakeWrapper(["gemini-empty"], ["gemini-empty"], keys_pool=["key"])
    try:
        wrapper.generate_content("дай текст", require_text=True)
    except RuntimeError as error:
        assert (
            "EmptyModelResponseError" in str(error)
            or "no candidate text" in str(error)
        )
    else:
        raise AssertionError("empty response was treated as success")


def test_generate_content_requires_text_by_default(monkeypatch):
    class EmptyResponse:
        text = None
        candidates = []

    class FakeGeminiModel:
        def generate_content(self, prompt):
            return EmptyResponse()

    class FakeWrapper(ModelFallbackWrapper):
        def _build_model(self, api_key, model_name):
            return FakeGeminiModel()

    monkeypatch.setattr(
        "infrastructure.ai.gemini._throttle_key",
        lambda api_key: None,
    )

    wrapper = FakeWrapper(["gemini-empty"], ["gemini-empty"], keys_pool=["key"])
    try:
        wrapper.generate_content("дай текст")
    except RuntimeError as error:
        assert "no candidate text" in str(error)
    else:
        raise AssertionError("generate_content accepted an empty text response")


def test_require_text_falls_back_to_next_model_on_empty_response(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, text):
            self.text = text
            self.candidates = []

    class FakeGeminiModel:
        def __init__(self, model_name):
            self.model_name = model_name

        def generate_content(self, prompt):
            calls.append(self.model_name)
            if self.model_name == "gemini-empty":
                return FakeResponse(None)
            return FakeResponse("готовый ответ")

    class FakeWrapper(ModelFallbackWrapper):
        def _build_model(self, api_key, model_name):
            return FakeGeminiModel(model_name)

    monkeypatch.setattr(
        "infrastructure.ai.gemini._throttle_key",
        lambda api_key: None,
    )

    wrapper = FakeWrapper(
        ["gemini-empty", "gemini-good"],
        ["gemini-empty", "gemini-good"],
        keys_pool=["key"],
    )
    result = wrapper.generate_content("дай текст")

    assert result.text == "готовый ответ"
    assert calls == ["gemini-empty", "gemini-good"]
    assert wrapper.last_used_model_name == "gemini-good"



class _FakeGeminiServerError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"{code} provider failure")
        self.code = code


def test_retryable_includes_gateway_and_service_failures():
    for code in (500, 502, 503, 504):
        assert _is_retryable(_FakeGeminiServerError(code))


def test_service_failures_open_model_circuit_and_fall_back(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, text):
            self.text = text
            self.candidates = []

    class FakeGeminiModel:
        def __init__(self, api_key, model_name):
            self.api_key = api_key
            self.model_name = model_name

        def generate_content(self, prompt):
            calls.append((self.model_name, self.api_key))
            if self.model_name == "gemini-bad":
                raise _FakeGeminiServerError(503)
            return FakeResponse("готово")

    class FakeWrapper(ModelFallbackWrapper):
        def _build_model(self, api_key, model_name):
            return FakeGeminiModel(api_key, model_name)

    monkeypatch.setattr(
        "infrastructure.ai.gemini._throttle_key",
        lambda api_key: None,
    )

    wrapper = FakeWrapper(
        ["gemini-bad", "gemini-good"],
        ["gemini-bad", "gemini-good"],
        keys_pool=["key-1", "key-2", "key-3"],
    )

    result = wrapper.generate_content("дай текст")
    assert result.text == "готово"
    assert calls == [
        ("gemini-bad", "key-1"),
        ("gemini-bad", "key-2"),
        ("gemini-good", "key-1"),
    ]

    calls.clear()
    second = wrapper.generate_content("ещё текст")
    assert second.text == "готово"
    assert calls == [("gemini-good", "key-2")]


def test_404_skips_missing_model_without_rotating_all_keys(monkeypatch):
    calls = []

    class FakeResponse:
        text = "готово"
        candidates = []

    class FakeGeminiModel:
        def __init__(self, api_key, model_name):
            self.api_key = api_key
            self.model_name = model_name

        def generate_content(self, prompt):
            calls.append((self.model_name, self.api_key))
            if self.model_name == "gemini-missing":
                raise _FakeGeminiServerError(404)
            return FakeResponse()

    class FakeWrapper(ModelFallbackWrapper):
        def _build_model(self, api_key, model_name):
            return FakeGeminiModel(api_key, model_name)

    monkeypatch.setattr(
        "infrastructure.ai.gemini._throttle_key",
        lambda api_key: None,
    )

    wrapper = FakeWrapper(
        ["gemini-missing", "gemini-good"],
        ["gemini-missing", "gemini-good"],
        keys_pool=["key-1", "key-2", "key-3"],
    )
    result = wrapper.generate_content("дай текст")

    assert result.text == "готово"
    assert calls == [
        ("gemini-missing", "key-1"),
        ("gemini-good", "key-1"),
    ]



def test_model_circuit_cooldown_escalates_and_success_resets(monkeypatch):
    from infrastructure.ai import gemini

    now = [1000.0]
    monkeypatch.setattr(gemini.time, "monotonic", lambda: now[0])

    wrapper = ModelFallbackWrapper(
        ["gemini-test"],
        ["gemini-test"],
        keys_pool=["key-1", "key-2"],
    )

    assert wrapper._record_model_transient_failure("gemini-test") is False
    assert wrapper._record_model_transient_failure("gemini-test") is True
    assert wrapper._model_circuit_remaining("gemini-test") == 60.0

    now[0] += 61.0
    assert wrapper._model_circuit_remaining("gemini-test") == 0.0
    assert wrapper._record_model_transient_failure("gemini-test") is False
    assert wrapper._record_model_transient_failure("gemini-test") is True
    assert wrapper._model_circuit_remaining("gemini-test") == 300.0

    now[0] += 301.0
    assert wrapper._model_circuit_remaining("gemini-test") == 0.0
    assert wrapper._record_model_transient_failure("gemini-test") is False
    assert wrapper._record_model_transient_failure("gemini-test") is True
    assert wrapper._model_circuit_remaining("gemini-test") == 900.0

    wrapper._record_model_success("gemini-test")
    assert wrapper._model_circuit_remaining("gemini-test") == 0.0

    assert wrapper._record_model_transient_failure("gemini-test") is False
    assert wrapper._record_model_transient_failure("gemini-test") is True
    assert wrapper._model_circuit_remaining("gemini-test") == 60.0


def test_empty_response_details_include_provider_metadata():
    class PromptFeedback:
        block_reason = "SAFETY"
        safety_ratings = ["prompt-rating"]

    class Candidate:
        finish_reason = "MAX_TOKENS"
        finish_message = "Stopped before text output"
        safety_ratings = ["candidate-rating"]

    class Response:
        prompt_feedback = PromptFeedback()
        candidates = [Candidate()]

    details = _empty_response_details(Response())

    assert "prompt_block_reason=SAFETY" in details
    assert "prompt_safety_ratings=['prompt-rating']" in details
    assert "candidate_0_finish_reason=MAX_TOKENS" in details
    assert "candidate_0_finish_message=Stopped before text output" in details
    assert "candidate_0_safety_ratings=['candidate-rating']" in details


def test_empty_response_details_report_zero_candidates():
    class Response:
        prompt_feedback = None
        candidates = []

    assert _empty_response_details(Response()) == "candidates=0"
