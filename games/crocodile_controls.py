"""Runtime controls for Crocodile round ownership and word navigation."""

from __future__ import annotations

import math
import time

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from games import crocodile
from games import crocodile_persistence as persistence


STOP_UNLOCK_SECONDS = 5 * 60
WORD_BACK_HISTORY_LIMIT = 30

_original_start_new_game = crocodile.start_new_game
_original_handle_callback = crocodile.handle_callback
_original_get_game_keyboard = crocodile.get_game_keyboard
_original_session_to_record = persistence._session_to_record
_original_session_from_record = persistence._session_from_record
_configured = False


def _session_started_at(session: dict) -> float:
    try:
        return float(session.get("started_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def stop_lock_remaining_seconds(
    session: dict,
    user_id: int,
    *,
    now: float | None = None,
) -> float:
    """Return seconds until a non-drawer may stop/replace the active round."""
    if int(session.get("drawer_id") or 0) == int(user_id):
        return 0.0

    started_at = _session_started_at(session)
    # Legacy/restored sessions created before this guard have no reliable start
    # timestamp. Do not lock them for a fresh 5 minutes after deployment.
    if started_at <= 0:
        return 0.0

    current_time = time.time() if now is None else float(now)
    elapsed = max(0.0, current_time - started_at)
    return max(0.0, STOP_UNLOCK_SECONDS - elapsed)


def can_stop_round(session: dict, user_id: int, *, now: float | None = None) -> bool:
    return stop_lock_remaining_seconds(session, user_id, now=now) <= 0


def stop_lock_message(
    session: dict,
    user_id: int,
    *,
    now: float | None = None,
) -> str | None:
    remaining = stop_lock_remaining_seconds(session, user_id, now=now)
    if remaining <= 0:
        return None
    minutes = max(1, math.ceil(remaining / 60))
    drawer_name = str(session.get("drawer_name") or "художник")
    return (
        f"Сейчас остановить игру может только {drawer_name}. "
        f"Остальным — через {minutes} мин."
    )


def remember_current_word(session: dict) -> None:
    current = str(session.get("word") or "").strip()
    if not current:
        return
    history = session.setdefault("previous_words", [])
    if not isinstance(history, list):
        history = []
        session["previous_words"] = history
    if not history or history[-1] != current:
        history.append(current)
    if len(history) > WORD_BACK_HISTORY_LIMIT:
        del history[:-WORD_BACK_HISTORY_LIMIT]


def take_previous_word(session: dict) -> str | None:
    history = session.get("previous_words")
    if not isinstance(history, list) or not history:
        return None
    previous = str(history.pop()).strip()
    return previous or None


def get_game_keyboard_with_previous(chat_id: int) -> InlineKeyboardMarkup:
    keyboard = _original_get_game_keyboard(chat_id)
    rows = [list(row) for row in keyboard.inline_keyboard]
    if len(rows) < 2 or len(rows[1]) < 3:
        return keyboard

    word_button, next_button, stop_button = rows[1][:3]
    previous_button = InlineKeyboardButton(
        text="↩️ Предыдущее",
        callback_data=f"cr_p_{chat_id}",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            rows[0],
            [word_button, next_button, previous_button],
            [stop_button],
        ]
    )


async def start_new_game_with_controls(
    chat_id: int,
    user_id: int,
    user_full_name: str,
) -> bool:
    cid = str(chat_id)
    current_session = crocodile.game_sessions.get(cid)
    if current_session and not can_stop_round(current_session, user_id):
        return False

    await _original_start_new_game(chat_id, user_id, user_full_name)
    session = crocodile.game_sessions.get(cid)
    if session is not None:
        session["started_at"] = time.time()
        session["previous_words"] = []
    return True


async def handle_start_game_with_controls(message: types.Message):
    cid = str(message.chat.id)
    session = crocodile.game_sessions.get(cid)
    user = message.from_user
    user_id = int(user.id) if user else 0
    if session:
        lock_message = stop_lock_message(session, user_id)
        if lock_message:
            await message.reply(lock_message)
            return

    if not user:
        await message.reply("Не удалось определить, кто запускает игру.")
        return

    await start_new_game_with_controls(
        message.chat.id,
        user.id,
        user.full_name,
    )


async def handle_text_stop_with_controls(message: types.Message):
    cid = str(message.chat.id)
    session = crocodile.game_sessions.get(cid)
    if not session:
        await message.reply("Игра не запущена.")
        return

    user_id = int(message.from_user.id) if message.from_user else 0
    lock_message = stop_lock_message(session, user_id)
    if lock_message:
        await message.reply(lock_message)
        return

    await crocodile._stop_session(cid, reason="text stop")
    await message.reply("🛑 Игра остановлена.")
    await message.answer(
        crocodile.format_leaderboard(cid, "🏆 Рейтинг (текущий)"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _set_next_word(cb: types.CallbackQuery, chat_id: str, session: dict):
    is_drawer = bool(
        cb.from_user and cb.from_user.id == session.get("drawer_id")
    )
    if not is_drawer:
        return await cb.answer(
            "Менять слово может только загадывающий 🔒",
            show_alert=True,
        )

    remember_current_word(session)
    new_word = crocodile._pick_word()
    session["word"] = new_word
    room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
    await crocodile.sio.emit("new_word_data", {"word": new_word}, room=room)
    return await cb.answer(f"Новое: {new_word.upper()}", show_alert=True)


async def _set_previous_word(
    cb: types.CallbackQuery,
    chat_id: str,
    session: dict,
):
    is_drawer = bool(
        cb.from_user and cb.from_user.id == session.get("drawer_id")
    )
    if not is_drawer:
        return await cb.answer(
            "Менять слово может только загадывающий 🔒",
            show_alert=True,
        )

    previous = take_previous_word(session)
    if not previous:
        return await cb.answer("Предыдущего слова нет", show_alert=True)

    session["word"] = previous
    room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
    await crocodile.sio.emit("new_word_data", {"word": previous}, room=room)
    return await cb.answer(f"Вернул: {previous.upper()}", show_alert=True)


async def handle_callback_with_controls(cb: types.CallbackQuery):
    data = cb.data or ""

    if data == "btn_want_draw":
        chat_id = str(cb.message.chat.id)
        session = crocodile.game_sessions.get(chat_id)
        user_id = int(cb.from_user.id)
        if session:
            lock_message = stop_lock_message(session, user_id)
            if lock_message:
                return await cb.answer(lock_message, show_alert=True)

        await cb.answer("Готовим холст...")
        await start_new_game_with_controls(
            cb.message.chat.id,
            cb.from_user.id,
            cb.from_user.full_name,
        )
        return

    if not data.startswith("cr_"):
        return await _original_handle_callback(cb)

    chat_id = data.split("_")[-1]
    session = crocodile.game_sessions.get(chat_id)
    if not session:
        return await cb.answer("Игра уже закончилась")

    if data.startswith("cr_n_"):
        return await _set_next_word(cb, chat_id, session)

    if data.startswith("cr_p_"):
        return await _set_previous_word(cb, chat_id, session)

    if data.startswith("cr_stop_"):
        user_id = int(cb.from_user.id)
        lock_message = stop_lock_message(session, user_id)
        if lock_message:
            return await cb.answer(lock_message, show_alert=True)

    return await _original_handle_callback(cb)


def session_to_record_with_controls(chat_id: str, session: dict) -> dict:
    record = _original_session_to_record(chat_id, session)
    record["started_at"] = _session_started_at(session)
    history = session.get("previous_words", [])
    if isinstance(history, list):
        record["previous_words"] = [
            str(word)
            for word in history[-WORD_BACK_HISTORY_LIMIT:]
            if str(word).strip()
        ]
    else:
        record["previous_words"] = []
    return record


def session_from_record_with_controls(record: dict) -> tuple[str, dict]:
    chat_id, session = _original_session_from_record(record)
    try:
        session["started_at"] = float(record.get("started_at") or 0)
    except (TypeError, ValueError):
        session["started_at"] = 0.0

    history = record.get("previous_words", [])
    if isinstance(history, list):
        session["previous_words"] = [
            str(word)
            for word in history[-WORD_BACK_HISTORY_LIMIT:]
            if str(word).strip()
        ]
    else:
        session["previous_words"] = []
    return chat_id, session


def configure_crocodile_controls() -> None:
    """Install Crocodile ownership/word controls before session restore."""
    global _configured
    if _configured:
        return

    # Keep persistence's existing socket-room hardening and score paths active.
    persistence.configure_crocodile_runtime()

    crocodile.get_game_keyboard = get_game_keyboard_with_previous
    crocodile.start_new_game = start_new_game_with_controls
    crocodile.handle_start_game = handle_start_game_with_controls
    crocodile.handle_text_stop = handle_text_stop_with_controls
    crocodile.handle_callback = handle_callback_with_controls

    persistence._session_to_record = session_to_record_with_controls
    persistence._session_from_record = session_from_record_with_controls
    _configured = True
