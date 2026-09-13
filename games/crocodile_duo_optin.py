"""Two-stage opt-in for shared drawing in the regular Crocodile mode."""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from games import crocodile, crocodile_persistence


_configured = False
_base_game_keyboard = None


def _artist_ids(session: dict) -> list[int]:
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
        primary = int(session.get("drawer_id") or 0)
    except (TypeError, ValueError):
        primary = 0
    if primary > 0 and primary not in ids:
        ids.insert(0, primary)
    return ids


def _strip_duo_buttons(keyboard: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in keyboard.inline_keyboard:
        clean = [
            button
            for button in row
            if not str(button.callback_data or "").startswith("cr_duo_")
        ]
        if clean:
            rows.append(clean)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _join_keyboard(chat_id: int | str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✋ Стать вторым художником",
                    callback_data=f"cr_duo_join_{chat_id}",
                )
            ]
        ]
    )


def decorate_game_keyboard_with_duo_opt_in(
    chat_id: int,
    keyboard: InlineKeyboardMarkup,
) -> InlineKeyboardMarkup:
    """Show an artist-controlled duo action on an already rendered game keyboard."""
    keyboard = _strip_duo_buttons(keyboard)
    session = crocodile.game_sessions.get(str(chat_id))
    if session and len(_artist_ids(session)) >= 2:
        return keyboard

    rows = [list(row) for row in keyboard.inline_keyboard]
    if session and session.get("duo_invite_open"):
        text = "✋ Стать вторым художником"
        callback_data = f"cr_duo_join_{chat_id}"
    else:
        text = "👥 Позвать второго — решает художник"
        callback_data = f"cr_duo_invite_{chat_id}"
    rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def decorate_party_menu_without_default_duo(
    keyboard: InlineKeyboardMarkup,
) -> InlineKeyboardMarkup:
    """Render classic as a single-player start; duo is unlocked by the artist."""
    rows: list[list[InlineKeyboardButton]] = []
    for row in keyboard.inline_keyboard:
        rendered: list[InlineKeyboardButton] = []
        for button in row:
            if button.callback_data == "cmenu_classic":
                rendered.append(
                    InlineKeyboardButton(text="🎨 Обычный", callback_data="cmenu_classic")
                )
            else:
                rendered.append(button)
        rows.append(rendered)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _persist_regular_state() -> None:
    try:
        crocodile_persistence.persist_crocodile_sessions(force=True)
    except Exception:
        logging.exception("[croc-duo] failed to persist duo opt-in state")


async def _edit_without_duo_button(callback, chat_id: str) -> None:
    try:
        if _base_game_keyboard is None:
            raise RuntimeError("duo base game keyboard is not configured")
        keyboard = _strip_duo_buttons(_base_game_keyboard(int(chat_id)))
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        logging.exception("[croc-duo] failed to update original game keyboard chat=%s", chat_id)


async def handle_duo_opt_in_callback(callback, next_handler) -> Any:
    """Handle duo callbacks or delegate unchanged callbacks downstream."""
    data = callback.data or ""
    if not data.startswith("cr_duo_"):
        return await next_handler(callback)

    if data.startswith("cr_duo_invite_"):
        chat_id = data[len("cr_duo_invite_"):]
        action = "invite"
    elif data.startswith("cr_duo_join_"):
        chat_id = data[len("cr_duo_join_"):]
        action = "join"
    else:
        # Compatibility with stale pre-opt-in buttons already sent to Telegram.
        chat_id = data[len("cr_duo_"):]
        action = "invite"

    session = crocodile.game_sessions.get(chat_id)
    if not session:
        return await callback.answer("Игра уже закончилась")
    user = callback.from_user
    if not user:
        return await callback.answer("Не удалось определить игрока")

    artist_ids = _artist_ids(session)
    try:
        primary_id = int(session.get("drawer_id") or 0)
    except (TypeError, ValueError):
        primary_id = 0
    primary_name = str(session.get("drawer_name") or "Художник")

    if action == "invite":
        if user.id != primary_id:
            return await callback.answer(
                "Сначала текущий художник должен сам позвать напарника.",
                show_alert=True,
            )
        if len(artist_ids) >= 2:
            return await callback.answer("Вы уже рисуете вдвоём 👥", show_alert=True)
        if session.get("duo_invite_open"):
            return await callback.answer("Напарника уже позвали — осталось кому-нибудь нажать кнопку.")

        session["duo_invite_open"] = True
        _persist_regular_state()
        await callback.answer("Открыл совместное рисование")
        await _edit_without_duo_button(callback, chat_id)
        await callback.message.answer(
            f"👥 <b>{html.escape(primary_name)}</b> решил рисовать не один. "
            "Теперь один желающий может присоединиться к общему холсту.",
            parse_mode="HTML",
            reply_markup=_join_keyboard(chat_id),
        )
        return

    if not session.get("duo_invite_open"):
        return await callback.answer(
            "Первый художник ещё не открывал совместное рисование.",
            show_alert=True,
        )
    if user.id == primary_id or user.id in artist_ids:
        return await callback.answer("Ты уже художник этого раунда.", show_alert=True)
    if len(artist_ids) >= 2:
        return await callback.answer("Второй художник уже нашёлся.", show_alert=True)

    session["drawer_ids"] = [primary_id, int(user.id)]
    session["drawer_names"] = [primary_name, user.full_name]
    session["drawer_name"] = f"{primary_name} + {user.full_name}"
    session["mode"] = "duo"
    session.pop("duo_invite_open", None)
    _persist_regular_state()

    await callback.answer("Ты второй хуйдожник. Открывай холст!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logging.exception("[croc-duo] failed to close join invitation chat=%s", chat_id)
    await callback.message.answer(
        f"👥 <b>{html.escape(primary_name)}</b> и <b>{html.escape(user.full_name)}</b> "
        "теперь рисуют вместе на одном холсте.",
        parse_mode="HTML",
    )


def enrich_session_record_with_duo_opt_in(
    chat_id: str,
    session: dict,
    record: dict,
) -> dict:
    """Persist an open duo invitation without replacing the base serializer."""
    if session.get("duo_invite_open") and len(_artist_ids(session)) < 2:
        record["duo_invite_open"] = True
    return record


def enrich_restored_session_with_duo_opt_in(
    record: dict,
    chat_id: str,
    session: dict,
) -> tuple[str, dict]:
    """Restore an open duo invitation after the base session is decoded."""
    if bool(record.get("duo_invite_open")) and len(_artist_ids(session)) < 2:
        session["duo_invite_open"] = True
    return chat_id, session


def configure_crocodile_duo_opt_in(*, base_game_keyboard) -> None:
    """Bind explicit dependencies used by the duo opt-in layer."""
    global _configured, _base_game_keyboard
    if _configured:
        return

    _base_game_keyboard = base_game_keyboard
    _configured = True
