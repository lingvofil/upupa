"""Compatibility facade for legacy ``AI.wrapper`` imports.

Provider-specific implementations moved to ``infrastructure.ai`` in R4.
New code should import the concrete adapter from that package instead.
"""

from infrastructure.ai.gemini import (
    EmptyModelResponseError,
    FallbackChatSession,
    GeminiModel,
    ModelFallbackWrapper,
    PER_KEY_MIN_DELAY,
    _build_config,
    _empty_response_details,
    _extract_error_details,
    _extract_response_text,
    _get_client,
    _is_retryable,
    _normalize_contents,
    _normalize_history,
    _normalize_part,
    _throttle_key,
)
from infrastructure.ai.gigachat import GigaChatWrapper
from infrastructure.ai.groq import GroqWrapper
from infrastructure.ai.openai_compatible import OpenAICompatibleWrapper


__all__ = [
    "EmptyModelResponseError",
    "FallbackChatSession",
    "GeminiModel",
    "ModelFallbackWrapper",
    "GigaChatWrapper",
    "GroqWrapper",
    "OpenAICompatibleWrapper",
    "PER_KEY_MIN_DELAY",
]
