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
from games import crocodile, crocodile_modes, crocodile_party_controls


_configured = False


def is_crocodile_admin(user_id: int | str | None) -> bool:
    try:
        return int(user_id or 0) == int(ADMIN_ID)
    except (TypeError, ValueError):
        return False


def _callback_user_id(callback) -> int | None:
    """Read callback user defensively; legacy tests/proxies may omit from_user."""
    return getattr(getattr(callback, "from_user", None), "id", None)


def stop_lock_remaining_seconds_with_admin(
    session: dict,
    user_id: int,
    next_handler,
    *,
    now: float | None = None,
) -> float:
    """The owner can stop/replace a regular round immediately."""
    if is_crocodile_admin(user_id):
        return 0.0
    return next_handler(session, user_id, now=now)


async def handle_telephone_callback_with_admin(callback, next_handler) -> Any:
    """Allow the owner to start/cancel/skip a telephone chain as a fallback."""
    data = callback.data or ""
    if not is_crocodile_admin(_callback_user_id(callback)):
        return await next_handler(callback)

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

    return await next_handler(callback)


async def handle_duel_callback_with_admin(callback, next_handler) -> Any:
    """Allow the owner to cancel a duel in any phase."""
    data = callback.data or ""
    if (
        is_crocodile_admin(_callback_user_id(callback))
        and data.startswith("cduel_cancel_")
    ):
        chat_id = data[len("cduel_cancel_"):]
        duel = crocodile_modes.duel_games.get(chat_id)
        if not duel:
            return await callback.answer("Дуэль уже закончилась")
        await callback.answer("Отменено администратором")
        await crocodile_party_controls._cancel_duel(chat_id, duel)
        return
    return await next_handler(callback)


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


async def stop_active_party_with_admin(
    chat_id: str,
    user_id: int,
    next_handler,
) -> tuple[bool, str]:
    """Owner fallback for stopping any active Crocodile mode."""
    if not is_crocodile_admin(user_id):
        return await next_handler(chat_id, user_id)

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


def menu_keyboard_with_admin_emergency_stop(
    chat_id: int | str,
    next_handler,
) -> InlineKeyboardMarkup:
    """Expose the missing emergency-stop entry while reverse mode is active."""
    keyboard = next_handler(chat_id)
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


async def reverse_callback_with_admin(callback, next_handler) -> Any:
    """Owner may use the normal surrender button without waiting five minutes."""
    data = callback.data or ""
    if (
        is_crocodile_admin(_callback_user_id(callback))
        and data.startswith("rcroc_stop_")
    ):
        chat_id = data[len("rcroc_stop_"):]
        if await _stop_reverse_as_admin(chat_id):
            return await callback.answer("Остановлено администратором")
        return await callback.answer("Игра уже закончилась")
    return await next_handler(callback)


async def reverse_modes_callback_with_admin(callback, next_handler) -> Any:
    """Owner may stop themed/progressive reverse modes immediately too."""
    data = callback.data or ""
    if (
        is_crocodile_admin(_callback_user_id(callback))
        and data.startswith("rcrocm_stop_")
    ):
        rest = data[len("rcrocm_stop_"):]
        _mode, _, chat_id = rest.rpartition("_")
        if await _stop_reverse_as_admin(chat_id):
            return await callback.answer("Остановлено администратором")
        return await callback.answer("Игра уже закончилась")
    return await next_handler(callback)


def configure_crocodile_admin_controls() -> None:
    """Mark owner fallbacks configured after explicit runtime composition."""
    global _configured
    if _configured:
        return
    _configured = True
