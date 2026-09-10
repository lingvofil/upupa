"""Owner-only emergency controls for Crocodile party modes.

The bot owner may override operational locks (stop/start/cancel/skip) without
receiving gameplay-only privileges such as seeing or changing another artist's
secret word or voting as an artist.
"""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.settings import ADMIN_ID
from games import crocodile, crocodile_controls, crocodile_modes, crocodile_party_controls


_configured = False
_original_stop_lock_remaining_seconds = None
_original_handle_telephone_callback = None
_original_handle_duel_callback = None
_original_stop_active_party = None
_original_menu_keyboard = None
_original_reverse_callback = None
_original_reverse_modes_callback = None


def is_crocodile_admin(user_id: int | str | None) -> bool:
    try:
        return int(user_id or 0) == int(ADMIN_ID)
    except (TypeError, ValueError):
        return False


def stop_lock_remaining_seconds_with_admin(
    session: dict,
    user_id: int,
    *,
    now: float | None = None,
) -> float:
    """The owner can stop/replace a regular round immediately."""
    if is_crocodile_admin(user_id):
        return 0.0
    return _original_stop_lock_remaining_seconds(session, user_id, now=now)


async def handle_telephone_callback_with_admin(callback) -> Any:
    """Allow the owner to start/cancel/skip a telephone chain as a fallback."""
    data = callback.data or ""
    if not is_crocodile_admin(getattr(callback.from_user, "id", None)):
        return await _original_handle_telephone_callback(callback)

    if data.startswith("ctel_skip_"):
        chat_id = data[len("ctel_skip_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game or game.get("phase") != "playing":
            return await callback.answer("Эта цепочка уже закрыта")
        await callback.answer("Пропускаем (админ)")
        await crocodile_party_controls._skip_telephone(chat_id, game)
        return

    if data.startswith("ctel_cancel_"):
        chat_id = data[len("ctel_cancel_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game:
            return await callback.answer("Эта цепочка уже закрыта")
        await callback.answer("Отменено администратором")
        await crocodile_party_controls._cancel_telephone(chat_id, game)
        return

    if data.startswith("ctel_start_"):
        chat_id = data[len("ctel_start_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game or game.get("phase") != "lobby":
            return await callback.answer("Эта цепочка уже закрыта")
        if len(game.get("players", [])) < crocodile_modes.TELEPHONE_MIN_PLAYERS:
            return await callback.answer(
                f"Нужно минимум {crocodile_modes.TELEPHONE_MIN_PLAYERS} человека",
                show_alert=True,
            )
        game["phase"] = "playing"
        await callback.answer("Поехали (админ)")
        await crocodile_modes._send_telephone_step(chat_id, game)
        return

    return await _original_handle_telephone_callback(callback)


async def handle_duel_callback_with_admin(callback) -> Any:
    """Allow the owner to cancel a duel in any phase."""
    data = callback.data or ""
    if (
        is_crocodile_admin(getattr(callback.from_user, "id", None))
        and data.startswith("cduel_cancel_")
    ):
        chat_id = data[len("cduel_cancel_"):]
        duel = crocodile_modes.duel_games.get(chat_id)
        if not duel:
            return await callback.answer("Дуэль уже закончилась")
        await callback.answer("Отменено администратором")
        await crocodile_party_controls._cancel_duel(chat_id, duel)
        return
    return await _original_handle_duel_callback(callback)


async def _stop_reverse_as_admin(chat_id: str) -> bool:
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as reverse_modes

    session = reverse.games.get(str(chat_id))
    if not session:
        return False

    word = html.escape(str(session.get("word") or "?").upper())
    text = f"🛠 Раунд аварийно остановлен администратором. Ответ: <b>{word}</b>."
    mode = str(session.get("mode") or "")
    if mode in {"reveal", "movie", "cartoon", "proverb", "saying"}:
        await reverse_modes.finish_mode(str(chat_id), session, text)
    else:
        await reverse._finish_game(str(chat_id), text)
    return True


async def stop_active_party_with_admin(chat_id: str, user_id: int) -> tuple[bool, str]:
    """Owner fallback for stopping any active Crocodile mode."""
    if not is_crocodile_admin(user_id):
        return await _original_stop_active_party(chat_id, user_id)

    chat_id = str(chat_id)
    telephone = crocodile_modes.telephone_games.get(chat_id)
    if telephone:
        await crocodile_party_controls._cancel_telephone(chat_id, telephone)
        return True, "Телефон остановлен."

    duel = crocodile_modes.duel_games.get(chat_id)
    if duel:
        await crocodile_party_controls._cancel_duel(chat_id, duel)
        return True, "Дуэль остановлена."

    if chat_id in crocodile.game_sessions:
        await crocodile._stop_session(chat_id, reason="admin emergency stop")
        return True, "🛑 Игра остановлена администратором."

    if await _stop_reverse_as_admin(chat_id):
        return True, "Раунд наоборот остановлен."

    return False, "Игра не запущена."


def menu_keyboard_with_admin_emergency_stop(chat_id: int | str) -> InlineKeyboardMarkup:
    """Expose the missing emergency-stop entry while reverse mode is active."""
    keyboard = _original_menu_keyboard(chat_id)
    if not crocodile_party_controls._reverse_active(str(chat_id)):
        return keyboard
    if any(
        button.callback_data == "cmenu_stop"
        for row in keyboard.inline_keyboard
        for button in row
    ):
        return keyboard

    rows = [list(row) for row in keyboard.inline_keyboard]
    insert_at = next(
        (
            index
            for index, row in enumerate(rows)
            if any(
                button.callback_data in {"cmenu_ratings", "cmenu_gallery"}
                for button in row
            )
        ),
        len(rows),
    )
    rows.insert(
        insert_at,
        [InlineKeyboardButton(text="🛠 Стоп (админ)", callback_data="cmenu_stop")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def reverse_callback_with_admin(callback) -> Any:
    """Owner may use the normal surrender button without waiting five minutes."""
    data = callback.data or ""
    if (
        is_crocodile_admin(getattr(callback.from_user, "id", None))
        and data.startswith("rcroc_stop_")
    ):
        chat_id = data[len("rcroc_stop_"):]
        if await _stop_reverse_as_admin(chat_id):
            return await callback.answer("Остановлено администратором")
        return await callback.answer("Игра уже закончилась")
    return await _original_reverse_callback(callback)


async def reverse_modes_callback_with_admin(callback) -> Any:
    """Owner may stop themed/progressive reverse modes immediately too."""
    data = callback.data or ""
    if (
        is_crocodile_admin(getattr(callback.from_user, "id", None))
        and data.startswith("rcrocm_stop_")
    ):
        rest = data[len("rcrocm_stop_"):]
        _mode, _, chat_id = rest.rpartition("_")
        if await _stop_reverse_as_admin(chat_id):
            return await callback.answer("Остановлено администратором")
        return await callback.answer("Игра уже закончилась")
    return await _original_reverse_modes_callback(callback)


def configure_crocodile_admin_controls() -> None:
    """Install owner fallbacks after all ordinary Crocodile wrappers."""
    global _configured
    global _original_stop_lock_remaining_seconds
    global _original_handle_telephone_callback, _original_handle_duel_callback
    global _original_stop_active_party, _original_menu_keyboard
    global _original_reverse_callback, _original_reverse_modes_callback
    if _configured:
        return

    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as reverse_modes

    _original_stop_lock_remaining_seconds = crocodile_controls.stop_lock_remaining_seconds
    _original_handle_telephone_callback = crocodile_modes.handle_telephone_callback
    _original_handle_duel_callback = crocodile_modes.handle_duel_callback
    _original_stop_active_party = crocodile_party_controls._stop_active_party
    _original_menu_keyboard = crocodile_party_controls.menu_keyboard
    _original_reverse_callback = reverse.handle_callback
    _original_reverse_modes_callback = reverse_modes.handle_callback

    crocodile_controls.stop_lock_remaining_seconds = stop_lock_remaining_seconds_with_admin
    crocodile_modes.handle_telephone_callback = handle_telephone_callback_with_admin
    crocodile_modes.handle_duel_callback = handle_duel_callback_with_admin
    crocodile_party_controls._stop_active_party = stop_active_party_with_admin
    crocodile_party_controls.menu_keyboard = menu_keyboard_with_admin_emergency_stop
    reverse.handle_callback = reverse_callback_with_admin
    reverse_modes.handle_callback = reverse_modes_callback_with_admin
    _configured = True
