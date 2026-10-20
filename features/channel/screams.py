"""Rare screaming breakdowns for Upupa's autonomous channel."""

from __future__ import annotations

import logging
import random

from features.channel import service as base

SCREAM_PROBABILITY = 0.025
SCREAM_COOLDOWN_POSTS = 20
MAX_GENERATION_ATTEMPTS = 3

# В раздражении и хаосе башню срывает заметно чаще, в сонном и задумчивом состоянии — реже.
SCREAM_MOOD_MULTIPLIERS = {
    "chaotic": 3.0,
    "irritated": 2.0,
    "sleepy": 0.4,
    "thoughtful": 0.5,
}

_SCREAM_LETTERS = "АЫУОЭЯИ"
_SCREAM_LETTER_WEIGHTS = (6, 4, 3, 2, 1, 1, 1)


def scream_probability(mood: dict | None) -> float:
    name = str((mood or {}).get("name") or "")
    return SCREAM_PROBABILITY * SCREAM_MOOD_MULTIPLIERS.get(name, 1.0)


def should_try_scream(published_posts: list[dict], mood: dict | None, *, rng=random) -> bool:
    recent = published_posts[-SCREAM_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "scream" for post in recent):
        return False
    return rng.random() < scream_probability(mood)


def _scream_burst(rng) -> str:
    """Один вопль вроде «ЫАУАЫАААА»: соседние звуки разные, последний тянется."""
    sounds: list[str] = []
    previous = ""
    for _ in range(rng.randint(2, 6)):
        options = [
            (letter, weight)
            for letter, weight in zip(_SCREAM_LETTERS, _SCREAM_LETTER_WEIGHTS)
            if letter != previous
        ]
        letter = rng.choices([item[0] for item in options], weights=[item[1] for item in options], k=1)[0]
        sounds.append(letter * rng.randint(1, 2))
        previous = letter
    sounds[-1] = previous * rng.randint(4, 9)
    return "".join(sounds)


def generate_scream(*, rng=random) -> str:
    burst_count = rng.choices((1, 2, 3), weights=(7, 2, 1), k=1)[0]
    text = " ".join(_scream_burst(rng) for _ in range(burst_count))
    if rng.random() < 0.3:
        text += "!" * rng.randint(1, 4)
    return text


def prepare_scream(
    published_posts: list[dict],
    mood: dict,
    *,
    rng=random,
) -> tuple[str, dict] | None:
    """Без LLM: вопль собирается кодом, поэтому не может «развернуться» в осмысленную фразу."""
    if not should_try_scream(published_posts, mood, rng=rng):
        return None

    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        text = generate_scream(rng=rng)
        reason = base._validate_post(text, recent_posts)
        if not reason:
            return text, {
                "post_kind": "scream",
                "content_mode": "scream",
                "chat_context_used": False,
                "mood": mood.get("name"),
                "mood_posts_left": mood.get("posts_left"),
            }
        logging.warning("[channel] scream attempt %s rejected: %s", attempt, reason)

    return None
