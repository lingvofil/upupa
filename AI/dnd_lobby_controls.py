"""Pregame participant controls for leaving and rebuilding DnD characters."""
from __future__ import annotations

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


_REBUILD_CALLBACK = "dnd:lobby:rebuild"
_LEAVE_CALLBACK = "dnd:lobby:leave"


def _add_controls(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    callbacks = {
        button.callback_data
        for row in rows
        for button in row
        if getattr(button, "callback_data", None)
    }
    if _REBUILD_CALLBACK in callbacks or _LEAVE_CALLBACK in callbacks:
        return markup

    controls = [
        [InlineKeyboardButton(text="♻️ Пересобрать персонажа", callback_data=_REBUILD_CALLBACK)],
        [InlineKeyboardButton(text="🚪 Выйти из кампании", callback_data=_LEAVE_CALLBACK)],
    ]
    start_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any(getattr(button, "callback_data", None) == "dnd:lobby:start" for button in row)
        ),
        len(rows),
    )
    rows[start_index:start_index] = controls
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clear_participant_session_state(session, user_id: int) -> None:
    key = str(int(user_id))
    for attr in (
        "character_profiles",
        "profile_options",
        "heritage",
        "inventories",
        "reputations",
    ):
        value = getattr(session, attr, None)
        if isinstance(value, dict):
            value.pop(key, None)


def install_dnd_lobby_controls(dnd_router) -> None:
    """Add lobby buttons and callbacks once, after campaign mechanics are configured."""
    if getattr(dnd_router, "_upupa_dnd_lobby_controls_configured", False):
        return

    from AI import dnd
    from AI import dnd_campaign as campaign

    original_lobby_keyboard = campaign._lobby_keyboard

    def lobby_keyboard(session=None):
        return _add_controls(original_lobby_keyboard(session))

    campaign._lobby_keyboard = lobby_keyboard
    dnd._lobby_keyboard = lambda: lobby_keyboard()

    async def leave_callback(callback):
        if not callback.message:
            await callback.answer("Кнопка потерялась.")
            return
        session = dnd.dnd_sessions.get(callback.message.chat.id)
        if not session or session.mode != "participants" or session.state != "LOBBY":
            await callback.answer("Кампания уже началась — выйти через лобби поздно.", show_alert=True)
            return

        user_id = int(callback.from_user.id)
        key = str(user_id)
        if key not in session.participants:
            await callback.answer("Ты и так не участвуешь.", show_alert=True)
            return

        session.participants.pop(key, None)
        _clear_participant_session_state(session, user_id)
        dnd.persist_dnd_sessions()
        await callback.answer("Вышел из кампании.")
        await campaign._refresh_lobby(session, callback.bot)

    async def rebuild_callback(callback):
        if not callback.message:
            await callback.answer("Кнопка потерялась.")
            return
        session = dnd.dnd_sessions.get(callback.message.chat.id)
        if not session or session.mode != "participants" or session.state != "LOBBY":
            await callback.answer("Кампания уже началась — персонаж зафиксирован.", show_alert=True)
            return

        user_id = int(callback.from_user.id)
        key = str(user_id)
        if key not in session.participants:
            await callback.answer("Сначала нажми «Участвовать».", show_alert=True)
            return

        campaign._ensure(session)
        session.character_profiles[key] = {}
        session.profile_options.pop(key, None)
        dnd.persist_dnd_sessions()
        await callback.answer("Пересобираю персонажа.")
        await campaign._refresh_lobby(session, callback.bot)

        options = await campaign._generate_profile_options(dnd, session, user_id, "style")
        await callback.message.answer(
            campaign._profile_choice_text(
                "style",
                options,
                heading="♻️ Пересобираем. Выбери",
            ),
            reply_markup=campaign._profile_keyboard(user_id, "style", options),
        )

    dnd_router.callback_query.register(leave_callback, F.data == _LEAVE_CALLBACK)
    dnd_router.callback_query.register(rebuild_callback, F.data == _REBUILD_CALLBACK)
    dnd_router._upupa_dnd_lobby_controls_configured = True
