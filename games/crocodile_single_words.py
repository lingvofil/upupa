"""Single-word picker for Crocodile modes.

The source dictionary intentionally keeps phrases for possible future modes, but
regular Crocodile and reverse Crocodile share one persistent picker that exposes
only single-token words.
"""

from __future__ import annotations

import logging
import random

from games import crocodile
from games import crocodile_persistence as persistence


_configured = False


def _single_word_pool() -> dict[str, str]:
    """Return unique normalized single-token words from the shared dictionary."""
    unique_words: dict[str, str] = {}
    for raw_word in crocodile._load_words():
        word = str(raw_word).strip()
        if len(word.split()) != 1:
            continue
        key = crocodile._normalize_guess(word)
        if key and key not in unique_words:
            unique_words[key] = word
    return unique_words


def pick_single_crocodile_word() -> str:
    """Pick a non-repeating single word using Crocodile's persistent history."""
    unique_words = _single_word_pool()
    if not unique_words:
        logging.error("[crocodile] no single words available; using fallback")
        return "кот"

    used = persistence._load_word_history()
    cleaned_used: list[str] = []
    seen: set[str] = set()
    for key in used:
        if key in unique_words and key not in seen:
            cleaned_used.append(key)
            seen.add(key)
    used = cleaned_used

    available = [key for key in unique_words if key not in seen]
    if not available:
        carry_count = min(
            persistence.WORD_HISTORY_CARRYOVER,
            max(0, len(unique_words) - 1),
        )
        used = used[-carry_count:] if carry_count else []
        seen = set(used)
        available = [key for key in unique_words if key not in seen]

    chosen_key = random.choice(available)
    used.append(chosen_key)
    try:
        persistence._write_word_history(used)
    except Exception:
        logging.exception(
            "[crocodile] failed to persist single-word history path=%s",
            persistence.CROCODILE_WORD_HISTORY_PATH,
        )
    return unique_words[chosen_key]


def configure_crocodile_single_words() -> None:
    """Install the shared single-word picker for both Crocodile modes."""
    global _configured
    if _configured:
        return

    # Patch the persistence entry point too: configure_crocodile_runtime() may
    # run again during restore and must keep reinstalling the filtered picker.
    persistence.pick_crocodile_word = pick_single_crocodile_word
    crocodile._pick_word = pick_single_crocodile_word
    _configured = True
