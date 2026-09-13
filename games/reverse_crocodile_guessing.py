"""Guess handling shared by special reverse-Crocodile modes."""

from __future__ import annotations

from games import crocodile
from games import reverse_crocodile as reverse
from games import reverse_crocodile_modes as modes


async def check_special_answer(message) -> bool:
    """Handle exact/close guesses for every special reverse-Crocodile mode."""
    chat_id = str(message.chat.id)
    session = reverse.games.get(chat_id)
    if not session or session.get("mode") in (None, "word") or not message.text:
        return False

    secret = str(session.get("word") or "")
    if reverse._contains_reverse_answer(message.text, secret):
        await crocodile._safe_react_to_guess(
            message,
            crocodile.CORRECT_GUESS_REACTION,
        )
        return await modes.check_answer(message)

    guess = reverse._normalize_reverse_guess(message.text)
    normalized_secret = reverse._normalize_reverse_guess(secret)
    if crocodile._is_close_guess(guess, normalized_secret):
        await crocodile._safe_react_to_guess(
            message,
            crocodile.CLOSE_GUESS_REACTION,
        )

    return False
