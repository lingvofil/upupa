"""Lazy construction of configured AI provider resources.

Importing this module is intentionally side-effect free with respect to provider
SDK clients. The legacy module-level names stay stable through LazyResource, but
the real object is built only on first use.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from gigachat import GigaChat

from core.settings import (
    GEMINI_KEYS_POOL,
    GIGACHAT_API_KEY,
    GIGACHAT_MODEL_QUEUE_DEFAULT,
    GIGACHAT_MODEL_QUEUE_SPECIAL,
    GROQ_API_KEY,
    GROQ_AUDIO_MODEL,
    GROQ_SUMMARIZATION_MODEL,
    GROQ_TEXT_MODEL,
    GROQ_TTS_MODEL,
    GROQ_VISION_MODEL,
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL,
    OPENROUTER_API_KEY,
    PRIMARY_GEMINI_KEY,
    SILICONFLOW_API_KEY,
)
from infrastructure.ai.execution import run_ai_provider_call
from infrastructure.ai.gemini import ModelFallbackWrapper, create_gemini_client
from infrastructure.ai.gigachat import GIGACHAT_BASE_URL, GigaChatConversationWrapper
from infrastructure.ai.groq import GroqWrapper
from infrastructure.ai.openai_compatible import OpenAICompatibleWrapper


_UNSET = object()
_GOVERNED_METHODS = {
    "analyze_image",
    "generate_content",
    "generate_custom",
    "generate_text",
    "transcribe_audio",
}
_GOVERNED_MODELS_METHODS = {
    "embed_content",
    "generate_content",
}


class ProviderConfigurationError(RuntimeError):
    """A provider was requested without its optional credentials."""


def _require_credential(value: str | None, setting_name: str) -> str:
    if value and value.strip():
        return value
    raise ProviderConfigurationError(
        f"{setting_name} is required to use this AI provider"
    )


class _GovernedModelsProxy:
    """Guard direct ``genai.Client.models`` calls used outside fallback wrappers."""

    def __init__(self, resource_name: str, resource: "LazyResource"):
        self._resource_name = resource_name
        self._resource = resource
        self._method_cache: dict[str, Callable[..., Any]] = {}

    def _models(self):
        return getattr(self._resource.unwrap(), "models")

    def __getattr__(self, name: str):
        if name not in _GOVERNED_MODELS_METHODS:
            return getattr(self._models(), name)

        cached = self._method_cache.get(name)
        if cached is None:
            operation = f"{self._resource_name}.models.{name}"

            def governed(*args, _name=name, _operation=operation, **kwargs):
                value = getattr(self._models(), _name)
                return run_ai_provider_call(_operation, value, *args, **kwargs)

            cached = governed
            self._method_cache[name] = cached
        return cached


class _GovernedChatSession:
    """Keep chat-session sends under the same provider limit as one-shot calls."""

    def __init__(self, resource_name: str, target: Any):
        self._resource_name = resource_name
        self._target = target
        self._method_cache: dict[str, Callable[..., Any]] = {}

    def __getattr__(self, name: str):
        value = getattr(self._target, name)
        if name != "send_message" or not callable(value):
            return value
        cached = self._method_cache.get(name)
        if cached is None:
            operation = f"{self._resource_name}.chat.send_message"

            def governed(*args, _value=value, _operation=operation, **kwargs):
                return run_ai_provider_call(_operation, _value, *args, **kwargs)

            cached = governed
            self._method_cache[name] = cached
        return cached


class LazyResource:
    """Thread-safe proxy that constructs one configured resource on first use."""

    def __init__(self, name: str, factory: Callable[[], Any]):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_value", _UNSET)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_proxy_cache", {})

    @property
    def initialized(self) -> bool:
        return object.__getattribute__(self, "_value") is not _UNSET

    def unwrap(self):
        value = object.__getattribute__(self, "_value")
        if value is _UNSET:
            lock = object.__getattribute__(self, "_lock")
            with lock:
                value = object.__getattribute__(self, "_value")
                if value is _UNSET:
                    factory = object.__getattribute__(self, "_factory")
                    value = factory()
                    object.__setattr__(self, "_value", value)
        return value

    def __getattr__(self, name: str):
        resource_name = object.__getattribute__(self, "_name")
        cache: dict[str, Any] = object.__getattribute__(self, "_proxy_cache")

        if name == "models":
            cached = cache.get(name)
            if cached is None:
                cached = _GovernedModelsProxy(resource_name, self)
                cache[name] = cached
            return cached

        if name == "start_chat":
            cached = cache.get(name)
            if cached is None:
                def governed_start_chat(*args, **kwargs):
                    value = getattr(self.unwrap(), "start_chat")
                    session = value(*args, **kwargs)
                    return _GovernedChatSession(resource_name, session)

                cached = governed_start_chat
                cache[name] = cached
            return cached

        if name in _GOVERNED_METHODS:
            cached = cache.get(name)
            if cached is None:
                operation = f"{resource_name}.{name}"

                def governed(*args, _name=name, _operation=operation, **kwargs):
                    value = getattr(self.unwrap(), _name)
                    return run_ai_provider_call(_operation, value, *args, **kwargs)

                cached = governed
                cache[name] = cached
            return cached

        return getattr(self.unwrap(), name)

    def __setattr__(self, name: str, value) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(self.unwrap(), name, value)

    def __call__(self, *args, **kwargs):
        return self.unwrap()(*args, **kwargs)

    def __enter__(self):
        return self.unwrap().__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self.unwrap().__exit__(exc_type, exc, tb)

    def __bool__(self) -> bool:
        return bool(self.unwrap())

    def __repr__(self) -> str:
        state = "initialized" if self.initialized else "pending"
        name = object.__getattribute__(self, "_name")
        return f"<LazyResource {name} ({state})>"


def _build_gemini_client():
    return create_gemini_client(
        _require_credential(PRIMARY_GEMINI_KEY, "GENERIC_API_KEY")
    )


def _build_groq():
    return GroqWrapper(
        _require_credential(GROQ_API_KEY, "GROQ_API_KEY"),
        vision_model=GROQ_VISION_MODEL,
        text_model=GROQ_TEXT_MODEL,
        audio_model=GROQ_AUDIO_MODEL,
        tts_model=GROQ_TTS_MODEL,
        summarization_model=GROQ_SUMMARIZATION_MODEL,
    )


def _build_gemini_fallback():
    return ModelFallbackWrapper(
        MODEL_QUEUE_DEFAULT,
        MODEL_QUEUE_SPECIAL,
        keys_pool=GEMINI_KEYS_POOL,
    )


def _build_gigachat_conversation():
    return GigaChatConversationWrapper(
        _require_credential(GIGACHAT_API_KEY, "GIGACHAT_API_KEY"),
        GIGACHAT_MODEL_QUEUE_DEFAULT,
        GIGACHAT_MODEL_QUEUE_SPECIAL,
    )


def _build_legacy_gigachat():
    return GigaChat(
        credentials=_require_credential(GIGACHAT_API_KEY, "GIGACHAT_API_KEY"),
        base_url=GIGACHAT_BASE_URL,
        model="GigaChat-2",
        verify_ssl_certs=False,
    )


def _build_openrouter():
    return OpenAICompatibleWrapper(
        api_key=_require_credential(OPENROUTER_API_KEY, "OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model_name="openrouter/elephant-alpha",
    )


def _build_siliconflow():
    return OpenAICompatibleWrapper(
        api_key=_require_credential(SILICONFLOW_API_KEY, "SILICONFLOW_API_KEY"),
        base_url="https://api.siliconflow.com/v1",
        model_name="deepseek-ai/DeepSeek-V3.2",
    )


gemini_client = LazyResource("gemini_client", _build_gemini_client)
groq_ai = LazyResource("groq_ai", _build_groq)
model = LazyResource("model", _build_gemini_fallback)
gigachat_model = LazyResource("gigachat_model", _build_gigachat_conversation)
gigachat = LazyResource("gigachat", _build_legacy_gigachat)
openrouter_ai = LazyResource("openrouter_ai", _build_openrouter)
siliconflow_ai = LazyResource("siliconflow_ai", _build_siliconflow)


__all__ = [
    "LazyResource",
    "ProviderConfigurationError",
    "gemini_client",
    "groq_ai",
    "model",
    "gigachat_model",
    "gigachat",
    "openrouter_ai",
    "siliconflow_ai",
]
