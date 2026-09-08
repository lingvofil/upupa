"""Priority handlers for Crocodile answers that collide with bot commands."""

from aiogram import Router, types

from features.crocodile_scoring import check_regular_answer
from games import crocodile, reverse_crocodile


router = Router(name="crocodile_guesses")


def is_regular_crocodile_answer(message: types.Message) -> bool:
    """Match only a real correct guess in an active regular Crocodile round."""
    if not message.text:
        return False

    session = crocodile.game_sessions.get(str(message.chat.id))
    if not session:
        return False

    from_user = message.from_user
    if from_user and from_user.id == session.get("drawer_id"):
        return False

    return crocodile._contains_answer(message.text, session.get("word", ""))


def is_reverse_crocodile_answer(message: types.Message) -> bool:
    """Match only a real correct guess in an active reverse Crocodile round."""
    if not message.text:
        return False

    session = reverse_crocodile.games.get(str(message.chat.id))
    if not session:
        return False

    return crocodile._contains_answer(message.text, session.get("word", ""))


@router.message(is_regular_crocodile_answer)
async def handle_regular_crocodile_answer(message: types.Message) -> None:
    """Finish the round before any command router can consume the guess."""
    await check_regular_answer(message)


@router.message(is_reverse_crocodile_answer)
async def handle_reverse_crocodile_answer(message: types.Message) -> None:
    """Finish the reverse round before any command router can consume the guess."""
    await reverse_crocodile.check_answer(message)
