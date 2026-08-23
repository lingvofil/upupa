"""Compatibility facade for the GigaChat conversation adapter.

The implementation lives in ``infrastructure.ai.gigachat`` since R4.
"""

from infrastructure.ai.gigachat import (
    GIGACHAT_BASE_URL,
    GigaChatConversationWrapper,
    GigaChatResponse,
)


__all__ = [
    "GIGACHAT_BASE_URL",
    "GigaChatConversationWrapper",
    "GigaChatResponse",
]
