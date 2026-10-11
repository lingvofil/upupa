"""Deterministic emotional valence of Telegram reactions.

Reactions are the only explicit emotional signal people give each other without
the bot reading message text.  The mapping is intentionally conservative:
ambiguous reactions (🤔, 🗿, 👀, 😐 …) stay neutral.
"""

from __future__ import annotations

from typing import Iterable


_VARIATION_SELECTORS = ("️", "︎")

POSITIVE_REACTIONS = frozenset({
    "👍", "❤", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "🙏", "👌", "😍", "❤‍🔥",
    "💯", "🤣", "⚡", "🏆", "🍾", "💋", "😇", "🤝", "🤗", "🫡", "😘", "💘",
    "🆒", "😎", "🕊", "🍓",
})

NEGATIVE_REACTIONS = frozenset({
    "👎", "🤬", "🤮", "💩", "🤡", "🖕", "🥱", "😡", "🤨",
})


def _normalize(emoji: str) -> str:
    value = str(emoji or "").strip()
    for selector in _VARIATION_SELECTORS:
        value = value.replace(selector, "")
    return value


def emoji_valence(emoji: str) -> int:
    value = _normalize(emoji)
    if value in POSITIVE_REACTIONS:
        return 1
    if value in NEGATIVE_REACTIONS:
        return -1
    return 0


def reaction_items_valence(items: Iterable[object]) -> int:
    """Collapse a set of Telegram ReactionType objects into -1, 0 or +1.

    A message can carry several reactions from one user.  Any negative reaction
    wins: «👍🤡» is sarcasm, not support.  Custom/paid reactions are neutral.
    """
    values = [emoji_valence(getattr(item, "emoji", None) or "") for item in items or ()]
    if any(value < 0 for value in values):
        return -1
    if any(value > 0 for value in values):
        return 1
    return 0


def reaction_names_valence(names) -> int:
    """Same rule as ``reaction_items_valence`` for stored emoji strings."""
    values = [emoji_valence(str(name or "")) for name in names or ()]
    if any(value < 0 for value in values):
        return -1
    if any(value > 0 for value in values):
        return 1
    return 0

