"""Priority handlers for Crocodile answers that collide with bot commands."""

from aiogram import Router, types

from features.crocodile_scoring import check_regular_answer
from games import (
    crocodile,
    crocodile_modes,
    reverse_crocodile,
    reverse_crocodile_modes,
)


router = Router(name="crocodile_guesses")


def is_regular_crocodile_answer(message: types.Message) -> bool:
    if not message.text:
        return False
    session = crocodile.game_sessions.get(str(message.chat.id))
    if not session:
        return False
    if message.from_user and crocodile_modes.is_session_drawer(session, message.from_user.id):
        return False
    return crocodile._contains_answer(message.text, session.get("word", ""))


def is_duel_crocodile_answer(message: types.Message) -> bool:
    if not message.text:
        return False
    duel = crocodile_modes.duel_games.get(str(message.chat.id))
    if not duel or duel.get("phase") != "drawing":
        return False
    if message.from_user and message.from_user.id in {int(row[0]) for row in duel.get("artists", [])}:
        return False
    return crocodile._contains_answer(message.text, duel.get("word", ""))


def is_reverse_crocodile_answer(message: types.Message) -> bool:
    if not message.text:
        return False
    session = reverse_crocodile.games.get(str(message.chat.id))
    if not session:
        return False
    return crocodile._contains_answer(message.text, session.get("word", ""))


@router.message(is_regular_crocodile_answer)
async def handle_regular_crocodile_answer(message: types.Message) -> None:
    await check_regular_answer(message)


@router.message(is_duel_crocodile_answer)
async def handle_duel_crocodile_answer(message: types.Message) -> None:
    await crocodile_modes.check_duel_answer(message)


@router.message(is_reverse_crocodile_answer)
async def handle_reverse_crocodile_answer(message: types.Message) -> None:
    session = reverse_crocodile.games.get(str(message.chat.id)) or {}
    if session.get("mode") not in (None, "word"):
        await reverse_crocodile_modes.check_answer(message)
    else:
        await reverse_crocodile.check_answer(message)
