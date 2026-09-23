from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + provider mocks)

from infrastructure.ai import gemini


class FakeProviderError(RuntimeError):
    def __init__(self, code: int, message: str | None = None):
        super().__init__(message or f"provider error {code}")
        self.code = code


def _response(text: str):
    return SimpleNamespace(text=text)


def _wrapper(keys):
    return gemini.ModelFallbackWrapper(
        default_queue=["m1", "m2"],
        special_queue=["m1", "m2"],
        keys_pool=list(keys),
    )


def _install_fake_models(monkeypatch, wrapper, handler):
    calls = []

    class FakeModel:
        def __init__(self, api_key, model_name):
            self.api_key = api_key
            self.model_name = model_name

        def generate_content(self, _prompt, **_kwargs):
            calls.append((self.model_name, self.api_key))
            outcome = handler(self.model_name, self.api_key)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(gemini, "_throttle_key", lambda _api_key: None)
    monkeypatch.setattr(
        wrapper,
        "_build_model",
        lambda api_key, model_name: FakeModel(api_key, model_name),
    )
    return calls


def test_single_key_429_rotates_without_opening_model_circuit(monkeypatch):
    wrapper = _wrapper(["k0", "k1", "k2"])

    def handler(model_name, api_key):
        if model_name == "m1" and api_key == "k0":
            return FakeProviderError(429)
        return _response("ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "ok"
    assert calls == [("m1", "k0"), ("m1", "k1")]
    assert wrapper.last_used_model_name == "m1"
    assert wrapper._model_rate_limit_circuit_remaining("m1") == 0
    assert "m1" not in wrapper._model_rate_limit_circuit_level


def test_mass_429_moves_to_next_model_before_full_key_pool(monkeypatch, caplog):
    wrapper = _wrapper(["k0", "k1", "k2", "k3", "k4"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(429)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert sum(model == "m1" for model, _ in calls) == 3
    assert sum(model == "m2" for model, _ in calls) == 1
    assert wrapper._model_rate_limit_circuit_remaining("m1") > 0
    assert "Gemini model 429 circuit opened" in caplog.text
    assert "reason=mass" in caplog.text


def test_full_pool_429_opens_model_circuit(monkeypatch, caplog):
    wrapper = _wrapper(["k0", "k1"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(429)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m1", "m1", "m2"]
    assert wrapper._model_rate_limit_circuit_remaining("m1") > 0
    assert "reason=full_pool" in caplog.text


def test_active_429_circuit_skips_model_on_subsequent_request(monkeypatch, caplog):
    wrapper = _wrapper(["k0", "k1"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(429)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    wrapper.generate_content("first", model_queue=["m1", "m2"])
    calls.clear()

    result = wrapper.generate_content("second", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m2"]
    assert "rate_limit_circuit_remaining_s=" in caplog.text


def test_success_after_429_cooldown_resets_rate_limit_state(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock[0])
    wrapper = _wrapper(["k0", "k1"])
    phase = {"rate_limited": True}

    def handler(model_name, _api_key):
        if model_name == "m1" and phase["rate_limited"]:
            return FakeProviderError(429)
        return _response(f"{model_name}-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    first = wrapper.generate_content("first", model_queue=["m1", "m2"])
    assert first.text == "m2-ok"
    assert wrapper._model_rate_limit_circuit_level["m1"] == 1

    phase["rate_limited"] = False
    clock[0] += gemini.MODEL_RATE_LIMIT_CIRCUIT_COOLDOWNS_SECONDS[0] + 1
    calls.clear()

    second = wrapper.generate_content("second", model_queue=["m1", "m2"])

    assert second.text == "m1-ok"
    assert calls[0][0] == "m1"
    assert "m1" not in wrapper._model_rate_limit_circuit_until
    assert "m1" not in wrapper._model_rate_limit_circuit_level


def test_existing_5xx_model_circuit_still_opens_after_two_failures(monkeypatch):
    wrapper = _wrapper(["k0", "k1", "k2"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(503)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m1", "m1", "m2"]
    assert wrapper._model_circuit_remaining("m1") > 0
    assert "m1" not in wrapper._model_rate_limit_circuit_until


def test_404_still_skips_missing_model_without_rotating_all_keys(monkeypatch):
    wrapper = _wrapper(["k0", "k1", "k2"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(404)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m1", "m2"]


def test_other_hard_errors_still_rotate_keys_before_fallback(monkeypatch):
    wrapper = _wrapper(["k0", "k1", "k2"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return FakeProviderError(400)
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m1", "m1", "m1", "m2"]


def test_empty_model_response_still_moves_directly_to_next_model(monkeypatch):
    wrapper = _wrapper(["k0", "k1", "k2"])

    def handler(model_name, _api_key):
        if model_name == "m1":
            return _response("")
        return _response("fallback-ok")

    calls = _install_fake_models(monkeypatch, wrapper, handler)

    result = wrapper.generate_content("prompt", model_queue=["m1", "m2"])

    assert result.text == "fallback-ok"
    assert [model for model, _ in calls] == ["m1", "m2"]
