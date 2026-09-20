"""Shared recognition of Telegram song commands.

This module intentionally depends only on message-shaped attributes so feature
routers and other transports can agree on whether a message belongs to the song
feature without importing handlers.
"""

from __future__ import annotations

import re


def _normalized_text(message) -> str:
    return re.sub(r"\s+", " ", str(getattr(message, "text", None) or "").strip())


def _text_mention_users(message) -> list:
    result = []
    for entity in getattr(message, "entities", None) or []:
        entity_type = str(getattr(entity, "type", "")).lower()
        user = getattr(entity, "user", None)
        if entity_type.endswith("text_mention") and user and not getattr(user, "is_bot", False):
            result.append(user)
    return result


def is_song_command(message) -> bool:
    """Return True for messages owned by the song command router."""
    text = _normalized_text(message)
    lowered = text.casefold()
    if lowered == "песня чат" or lowered.startswith("песня @"):
        return True
    return lowered.startswith("песня ") and bool(_text_mention_users(message))
