"""UI additions for Crocodile: instant word reveal, clearer next button, ratings."""

from __future__ import annotations

import contextvars
import html
import logging
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.loader import bot
from features.crocodile_scoring import (
    format_artist_leaderboard,
    format_slowest_artist_leaderboard,
)
from games import crocodile, crocodile_likes, crocodile_modes, crocodile_party_controls
from games import crocodile_ratings


_configured = False
_original_start_new_game = None
_original_get_game_keyboard = None
_original_get_end_game_keyboard = None
_original_handle_callback = None
_original_check_answer = None
_original_party_menu_keyboard = None
_original_party_menu_callback = None
_original_final_frame_handler = None

_suppress_private_word: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "crocodile_suppress_private_word", default=False
)
_final_like_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "crocodile_final_like_context", default=None
)
_active_like_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "crocodile_active_like_token", default=None
)


def _session_artists(session: dict | None) -> list[tuple[int, str]]:
    if not session:
        return []
    ids: list[int] = []
    raw_ids = session.get("drawer_ids")
    if isinstance(raw_ids, (list, tuple, set)):
        for value in raw_ids:
            try:
                user_id = int(value)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id not in ids:
                ids.append(user_id)
    try:
        primary_id = int(session.get("drawer_id") or 0)
    except (TypeError, ValueError):
        primary_id = 0
    if primary_id > 0 and primary_id not in ids:
        ids.insert(0, primary_id)

    raw_names = session.get("drawer_names")
    names = raw_names if isinstance(raw_names, list) else []
    primary_name = str(session.get("drawer_name") or "Художник")
    artists = []
    for index, user_id in enumerate(ids):
        if index < len(names) and str(names[index]).strip():
            name = str(names[index])
        elif len(ids) == 1:
            name = primary_name
        else:
            name = primary_name.split(" + ")[index] if " + " in primary_name and index < len(primary_name.split(" + ")) else primary_name
        artists.append((user_id, name))
    return artists


def _word_from_session(chat_id: int | str) -> str:
    session = crocodile.game_sessions.get(str(chat_id)) or {}
    return str(session.get("word") or "")


async def _send_word_privately(user_id: int, word: str) -> bool:
    if not word:
        return False
    try:
        await bot.send_message(
            int(user_id),
            f"🎯 Твоё слово: <b>{html.escape(word.upper())}</b>\n"
            "Кнопка «Слово» в чате остаётся на случай, если захочешь подсмотреть ещё раз.",
            parse_mode="HTML",
        )
        return True
    except Exception as exc:
        logging.info("[croc-ui] private word delivery unavailable user=%s: %s", user_id, exc)
        return False


async def start_new_game_with_instant_word(chat_id: int, user_id: int, user_full_name: str):
    """Start normally and immediately reveal the selected word to the artist."""
    result = await _original_start_new_game(chat_id, user_id, user_full_name)
    word = _word_from_session(chat_id)
    if word and not _suppress_private_word.get():
        await _send_word_privately(user_id, word)
    return result


def get_game_keyboard_with_clear_next(chat_id: int) -> InlineKeyboardMarkup:
    keyboard = _original_get_game_keyboard(chat_id)
    rows: list[list[InlineKeyboardButton]] = []
    for row in keyboard.inline_keyboard:
        rendered: list[InlineKeyboardButton] = []
        for button in row:
            if str(button.callback_data or "").startswith("cr_n_"):
                rendered.append(
                    InlineKeyboardButton(
                        text="⏭ Следующее",
                        callback_data=button.callback_data,
                    )
                )
            else:
                rendered.append(button)
        rows.append(rendered)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _like_button_with_token(keyboard: InlineKeyboardMarkup, token: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in keyboard.inline_keyboard:
        rendered: list[InlineKeyboardButton] = []
        for button in row:
            if button.callback_data == "btn_like":
                rendered.append(
                    InlineKeyboardButton(
                        text=button.text,
                        callback_data=f"cr_like_{token}",
                    )
                )
            else:
                rendered.append(button)
        rows.append(rendered)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_end_game_keyboard_with_attribution(likes: int = 0) -> InlineKeyboardMarkup:
    keyboard = _original_get_end_game_keyboard(likes)
    token = _active_like_token.get()
    if not token:
        context = _final_like_context.get()
        if context:
            token = crocodile_ratings.create_like_target(
                context.get("chat_id"), context.get("artists")
            )
    if not token:
        return keyboard
    return _like_button_with_token(keyboard, token)


def menu_keyboard_with_ratings(chat_id: int | str) -> InlineKeyboardMarkup:
    keyboard = _original_party_menu_keyboard(chat_id)
    rows = [list(row) for row in keyboard.inline_keyboard]
    if any(
        button.callback_data == "cmenu_ratings"
        for row in rows
        for button in row
    ):
        return keyboard
    rating_row = [
        InlineKeyboardButton(text="🏆 Рейтинги", callback_data="cmenu_ratings")
    ]
    gallery_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any(button.callback_data == "cmenu_gallery" for button in row)
        ),
        len(rows),
    )
    rows.insert(gallery_index, rating_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ratings_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧠 По угадываниям", callback_data="cmenu_rating_game"),
                InlineKeyboardButton(text="🎨 По рисункам", callback_data="cmenu_rating_artists"),
            ],
            [
                InlineKeyboardButton(text="❤️ По лайкам", callback_data="cmenu_rating_likes"),
            ],
            [
                InlineKeyboardButton(text="🐌 Долго в среднем", callback_data="cmenu_rating_slow"),
                InlineKeyboardButton(text="🐢 Самый долгий", callback_data="cmenu_rating_longest"),
            ],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="cmenu_main")],
        ]
    )


def rating_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К рейтингам", callback_data="cmenu_ratings")]
        ]
    )


def _format_game_rating(chat_id: int | str) -> str:
    if not crocodile._scores:
        crocodile._scores_load()
    return crocodile.format_leaderboard(str(chat_id), "🧠 Лучшие угадыватели")


def _rating_text(kind: str, chat_id: int | str) -> str:
    if kind == "game":
        return _format_game_rating(chat_id)
    if kind == "artists":
        return format_artist_leaderboard(chat_id)
    if kind == "likes":
        return crocodile_ratings.format_like_leaderboard(chat_id)
    if kind == "slow":
        return format_slowest_artist_leaderboard(chat_id)
    if kind == "longest":
        return crocodile_ratings.format_longest_single_draw_leaderboard(chat_id)
    return "🏆 Неизвестный рейтинг."


async def handle_party_menu_callback_with_ratings(callback) -> Any:
    data = callback.data or ""
    chat_id = str(callback.message.chat.id)

    if data == "cmenu_main":
        await callback.answer()
        await callback.message.edit_text(
            crocodile_party_controls.party_status_text(chat_id),
            reply_markup=crocodile_party_controls.menu_keyboard(chat_id),
        )
        return

    if data == "cmenu_ratings":
        await callback.answer()
        await callback.message.edit_text(
            "🏆 <b>Рейтинги кракадила</b>\nВыбирай, кто сегодня официально лучший или худший.",
            parse_mode="HTML",
            reply_markup=ratings_menu_keyboard(),
        )
        return

    if data.startswith("cmenu_rating_"):
        kind = data[len("cmenu_rating_"):]
        await callback.answer()
        await callback.message.edit_text(
            _rating_text(kind, chat_id),
            parse_mode="HTML",
            reply_markup=rating_back_keyboard(),
        )
        return

    if data == "cmenu_classic" and not crocodile_party_controls.has_active_party(chat_id):
        token = _suppress_private_word.set(True)
        try:
            await crocodile.start_new_game(
                callback.message.chat.id,
                callback.from_user.id,
                callback.from_user.full_name,
            )
        finally:
            _suppress_private_word.reset(token)
        word = _word_from_session(chat_id)
        if word:
            await callback.answer(f"🎯 Твоё слово: {word.upper()}", show_alert=True)
        else:
            await callback.answer("Готовим холст")
        return

    return await _original_party_menu_callback(callback)


def _message_like_key(callback) -> str:
    return crocodile_likes._message_key(
        callback.message.chat.id,
        callback.message.message_id,
    )


def _like_already_registered(callback) -> bool:
    try:
        record = crocodile_likes._get_registry().get(_message_like_key(callback), {})
        return int(callback.from_user.id) in record.get("users", [])
    except Exception:
        return False


def _is_target_artist(callback, target: dict | None) -> bool:
    if not target or not callback.from_user:
        return False
    user_id = int(callback.from_user.id)
    return any(
        int(artist.get("id", 0)) == user_id
        for artist in crocodile_ratings.normalize_artists(target.get("artists"))
    )


async def _handle_attributed_like(callback, token: str) -> None:
    target = crocodile_ratings.get_like_target(token)
    if target and str(target.get("chat_id")) != str(callback.message.chat.id):
        await callback.answer("Эта кнопка вообще от другого рисунка.", show_alert=True)
        return

    if _is_target_artist(callback, target):
        await callback.answer("Свой рисунок лайкать нельзя 😏", show_alert=True)
        return

    already_liked = _like_already_registered(callback)
    token_ctx = _active_like_token.set(token)
    try:
        await crocodile_likes.handle_like_callback(callback)
    finally:
        _active_like_token.reset(token_ctx)

    if already_liked:
        return
    if _like_already_registered(callback):
        try:
            crocodile_ratings.credit_like(token)
        except Exception:
            logging.exception("[croc-ui] failed to credit artist like token=%s", token)


async def handle_crocodile_callback_with_ui(callback) -> Any:
    data = callback.data or ""
    if data.startswith("cr_like_"):
        return await _handle_attributed_like(callback, data[len("cr_like_"):])

    if data == "btn_want_draw":
        token = _suppress_private_word.set(True)
        try:
            await crocodile.start_new_game(
                callback.message.chat.id,
                callback.from_user.id,
                callback.from_user.full_name,
            )
        finally:
            _suppress_private_word.reset(token)
        word = _word_from_session(callback.message.chat.id)
        if word:
            return await callback.answer(f"🎯 Твоё слово: {word.upper()}", show_alert=True)
        return await callback.answer("Готовим холст")

    return await _original_handle_callback(callback)


async def check_answer_with_like_context(message) -> bool:
    chat_id = str(message.chat.id)
    session = crocodile.game_sessions.get(chat_id)
    context = None
    if session:
        context = {"chat_id": chat_id, "artists": _session_artists(session)}
    token = _final_like_context.set(context)
    try:
        return await _original_check_answer(message)
    finally:
        _final_like_context.reset(token)


async def final_frame_with_like_context(sid, data):
    context = None
    try:
        _canonical, session_key = crocodile_modes.normalize_crocodile_room(
            data.get("room") if isinstance(data, dict) else None
        )
        if ":" not in session_key:
            session = crocodile.game_sessions.get(session_key)
            if session:
                context = {
                    "chat_id": session_key,
                    "artists": _session_artists(session),
                }
    except Exception:
        context = None

    token = _final_like_context.set(context)
    try:
        return await _original_final_frame_handler(sid, data)
    finally:
        _final_like_context.reset(token)


def configure_crocodile_ui_enhancements() -> None:
    """Install the final Crocodile UI layer after party and duo extensions."""
    global _configured
    global _original_start_new_game, _original_get_game_keyboard
    global _original_get_end_game_keyboard, _original_handle_callback
    global _original_check_answer, _original_party_menu_keyboard
    global _original_party_menu_callback, _original_final_frame_handler
    if _configured:
        return

    _original_start_new_game = crocodile.start_new_game
    _original_get_game_keyboard = crocodile.get_game_keyboard
    _original_get_end_game_keyboard = crocodile.get_end_game_keyboard
    _original_handle_callback = crocodile.handle_callback
    _original_check_answer = crocodile.check_answer
    _original_party_menu_keyboard = crocodile_party_controls.menu_keyboard
    _original_party_menu_callback = crocodile_party_controls.handle_menu_callback
    _original_final_frame_handler = crocodile_modes.final_frame_with_modes

    crocodile.start_new_game = start_new_game_with_instant_word
    crocodile.get_game_keyboard = get_game_keyboard_with_clear_next
    crocodile.get_end_game_keyboard = get_end_game_keyboard_with_attribution
    crocodile.handle_callback = handle_crocodile_callback_with_ui
    crocodile.check_answer = check_answer_with_like_context
    crocodile_party_controls.menu_keyboard = menu_keyboard_with_ratings
    crocodile_party_controls.handle_menu_callback = handle_party_menu_callback_with_ratings
    crocodile.sio.on("final_frame", handler=final_frame_with_like_context)
    _configured = True
