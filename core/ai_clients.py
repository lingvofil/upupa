"""Compatibility exports for configured AI provider resources.

Provider implementations and SDK construction live in ``infrastructure.ai``.
The exported objects are lazy proxies, so importing ``core.ai_clients`` does not
open provider sessions or construct SDK clients.
"""

from infrastructure.ai.clients import (
    gemini_client,
    gigachat,
    gigachat_model,
    groq_ai,
    model,
    openrouter_ai,
    siliconflow_ai,
)


__all__ = [
    "gemini_client",
    "groq_ai",
    "model",
    "gigachat_model",
    "gigachat",
    "openrouter_ai",
    "siliconflow_ai",
]
