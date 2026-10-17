"""Трансляция сообщений чата художнику во время «Крокодила».

WebApp открывается поверх Telegram-чата, и художник не видит, что пишут
отгадывающие. Outer-middleware ловит каждое текстовое сообщение в чате с
активным раундом и шлёт его в socket.io-комнату холста. В комнату входит
только авторизованный художник, так что сообщения никуда больше не утекают.

Middleware никогда не блокирует и не роняет обработку сообщения.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

from games import crocodile, crocodile_modes

MAX_TEXT_LENGTH = 140
MAX_NAME_LENGTH = 32


def _short(value: str, limit: int) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _target_rooms(chat_id: str, user_id: int) -> tuple[list[str], str]:
    """Комнаты художников, которым нужно показать сообщение, и загаданное слово."""
    session = crocodile.game_sessions.get(chat_id)
    if session and session.get("word"):
        if crocodile_modes.is_session_drawer(session, user_id):
            return [], ""
        return [crocodile_modes._chat_room(chat_id)], str(session["word"])

    duel = crocodile_modes.duel_games.get(chat_id)
    if duel and duel.get("phase") == "drawing" and duel.get("word"):
        artists = duel.get("artists") or []
        if user_id in {int(row[0]) for row in artists}:
            return [], ""
        rooms = [
            crocodile_modes._chat_room(chat_id, f"d{slot}")
            for slot in range(1, len(artists) + 1)
        ]
        return rooms, str(duel["word"])

    return [], ""


def build_guess_payload(message: Message, word: str) -> dict:
    text = _short(message.text or "", MAX_TEXT_LENGTH)
    user = message.from_user
    name = _short(
        (user.first_name or user.full_name or "Аноним") if user else "Аноним",
        MAX_NAME_LENGTH,
    )
    correct = crocodile._contains_answer(text, word)
    close = (not correct) and crocodile._is_close_guess(
        crocodile._normalize_guess(text), crocodile._normalize_guess(word)
    )
    return {"name": name, "text": text, "correct": correct, "close": close}


async def relay_chat_message(message: Message) -> bool:
    if not isinstance(message, Message) or not message.text or not message.from_user:
        return False
    if message.text.startswith("/"):
        return False
    chat_id = str(message.chat.id)
    rooms, word = _target_rooms(chat_id, message.from_user.id)
    if not rooms:
        return False

    payload = build_guess_payload(message, word)
    for room in rooms:
        await crocodile.sio.emit("chat_guess", payload, room=room)
    return True


class CrocodileChatRelayMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        try:
            await relay_chat_message(event)
        except Exception as exc:  # трансляция не должна ломать бота
            logging.warning("[croc-chat-relay] failed: %s", exc)
        return await handler(event, data)
